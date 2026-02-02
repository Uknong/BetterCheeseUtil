"""
ChzzkOverlay Subprocess Entry Point

This module runs ChzzkOverlay as a completely separate QApplication process.
It receives commands via TCP socket and sends frames via shared memory.
"""

import sys
import os
import socket
import threading
import struct
from multiprocessing import shared_memory

# Add parent path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.dirname(script_dir)
root_dir = os.path.dirname(app_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from PyQt6.QtWidgets import QApplication, QMainWindow
from PyQt6.QtCore import Qt, QTimer, QEvent, QPointF, QUrl, pyqtSignal, QObject
from PyQt6.QtGui import QMouseEvent, QCloseEvent, QKeyEvent, QImage
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEngineProfile, QWebEngineScript, QWebEnginePage, QWebEngineSettings

from app.overlay.ipc_protocol import (
    IPC_PORT, SHARED_MEMORY_NAME, FRAME_WIDTH, FRAME_HEIGHT, FRAME_CHANNELS, FRAME_SIZE,
    CommandType, EventType, IPCMessage,
    evt_video_started, evt_resolution_detected, evt_overlay_closed, evt_position_changed, evt_ready, evt_pong
)

# Constants
USERPATH = os.path.expanduser("~")

# JavaScript injection code (copied from ui_dialogs.py)
INJECT_JS = """
(function() {
    // Fake getHighEntropyValues for navigator.userAgentData
    if (navigator.userAgentData) {
        navigator.userAgentData.getHighEntropyValues = function(hints) {
            return Promise.resolve({
                brands: navigator.userAgentData.brands || [],
                mobile: false,
                platform: 'Windows',
                platformVersion: '10.0.0',
                architecture: 'x86',
                model: '',
                uaFullVersion: '130.0.0.0',
                fullVersionList: []
            });
        };
    }

    // 8. BCU Force Connect (Buffer Reset) - 영상 강제 연결
    (function() {
        // Iframe Logic: Listen for force connect command
        if (window.top !== window.self) {
             window.addEventListener('message', function(e) {
                 if (e.data === 'BCU_FORCE_CONNECT') {
                     console.log('[BCU] Force connect executing in iframe...');
                     
                     // Strategy 1: Direct Player Object API (Best for YouTube)
                     var player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
                     if (player && player.seekTo && typeof player.seekTo === 'function') {
                         console.log('[BCU] Using movie_player API');
                         try {
                             var dur = player.getDuration();
                             var curr = player.getCurrentTime();
                             
                             // Seek to near end to clear "end" state
                             player.seekTo(dur - 3, true);
                             
                             setTimeout(function() {
                                 // Seek back to original time
                                 player.seekTo(curr, true);
                                 player.playVideo();
                             }, 10);
                             return; // Success
                         } catch(err) {
                             console.log('[BCU] Player API error:', err);
                         }
                     }
                     
                     // Strategy 2: PostMessage (Fallback)
                     // ... (omitted as it failed previously, straight to fallback or done)
                     
                     // Strategy 3: HTML5 Video Element (Ultimate Fallback)
                     var v = document.querySelector('video');
                     if (v && v.duration) {
                         console.log('[BCU] Using HTML5 Video API Fallback');
                         let t = v.currentTime;
                         let d = v.duration;
                         
                         v.currentTime = d - 3;
                         setTimeout(function() {
                            v.currentTime = t;
                            v.play();
                         }, 10);
                     }
                 }
             });
        }

        // Main Frame Logic
        window.bcuForceConnect = function() {
            console.log('[BCU] Force connect triggered');
            
            // 1. Send command to all iframes
            const iframes = document.getElementsByTagName('iframe');
            for (let i = 0; i < iframes.length; i++) {
                try {
                    iframes[i].contentWindow.postMessage('BCU_FORCE_CONNECT', '*');
                } catch(e) {}
            }

            // 2. Check local video tags (Direct video file or main frame only)
            // Just for safety, try player API here too
            var player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
             if (player && player.seekTo && typeof player.seekTo === 'function') {
                 try {
                     var dur = player.getDuration();
                     var curr = player.getCurrentTime();
                     player.seekTo(dur - 3, true);
                     setTimeout(function() {
                         player.seekTo(curr, true);
                         player.playVideo();
                     }, 10);
                     return;
                 } catch(e) {}
             }

            document.querySelectorAll('video').forEach(v => {
                if(v.duration) {
                    let t = v.currentTime;
                    let d = v.duration;
                    v.currentTime = Math.max(0, d - 3);
                    setTimeout(() => {
                        v.currentTime = t;
                        v.play();
                    }, 10);
                }
            });
        };
    })();

    // 9. BCU Seek To Start - 영상 맨 앞으로 이동 및 일시정지
    (function() {
        // Iframe Logic
        if (window.top !== window.self) {
             window.addEventListener('message', function(e) {
                 if (e.data === 'BCU_SEEK_TO_START') {
                     console.log('[BCU] Seek to start in iframe...');
                     var player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
                     if (player && player.seekTo) {
                         try {
                             player.seekTo(0, true);
                             player.pauseVideo();
                             return;
                         } catch(e) {}
                     }
                     var v = document.querySelector('video');
                     if (v) {
                         v.currentTime = 0;
                         v.pause();
                     }
                 }
             });
        }
        
        // Main Frame Logic
        window.bcuSeekToStart = function() {
            console.log('[BCU] Seek to start triggered');
            const iframes = document.getElementsByTagName('iframe');
            for (let i = 0; i < iframes.length; i++) {
                try { iframes[i].contentWindow.postMessage('BCU_SEEK_TO_START', '*'); } catch(e) {}
            }
            var player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
            if (player && player.seekTo) {
                try { player.seekTo(0, true); player.pauseVideo(); return; } catch(e) {}
            }
            document.querySelectorAll('video').forEach(v => {
                v.currentTime = 0;
                v.pause();
            });
        };
    })();

    // 10. BCU Toggle Play/Pause - 재생/정지 토글
    (function() {
        // Iframe Logic
        if (window.top !== window.self) {
             window.addEventListener('message', function(e) {
                 if (e.data === 'BCU_TOGGLE_PLAY_PAUSE') {
                     console.log('[BCU] Toggle play/pause in iframe...');
                     var player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
                     if (player && player.getPlayerState) {
                         try {
                             var state = player.getPlayerState();
                             if (state === 1) { player.pauseVideo(); }
                             else { player.playVideo(); }
                             return;
                         } catch(e) {}
                     }
                     var v = document.querySelector('video');
                     if (v) {
                         if (v.paused) { v.play(); }
                         else { v.pause(); }
                     }
                 }
             });
        }
        
        // Main Frame Logic
        window.bcuTogglePlayPause = function() {
            console.log('[BCU] Toggle play/pause triggered');
            const iframes = document.getElementsByTagName('iframe');
            for (let i = 0; i < iframes.length; i++) {
                try { iframes[i].contentWindow.postMessage('BCU_TOGGLE_PLAY_PAUSE', '*'); } catch(e) {}
            }
            var player = document.getElementById('movie_player') || document.querySelector('.html5-video-player');
            if (player && player.getPlayerState) {
                try {
                    var state = player.getPlayerState();
                    if (state === 1) { player.pauseVideo(); } else { player.playVideo(); }
                    return;
                } catch(e) {}
            }
            document.querySelectorAll('video').forEach(v => {
                if (v.paused) { v.play(); } else { v.pause(); }
            });
        };
    })();

    // 1. Skip logic
    const TARGET_MIN = 2500;
    const TARGET_MAX = 3500;
    const SKIP_COOLDOWN = 500;
    let lastSkipTime = 0;
    const originalSetTimeout = window.setTimeout;
    
    // Default enabled
    window.bcu_skip_timer_enabled = true;

    if (window.top === window.self) console.log('[Overlay] Script Loaded. Overwriting setTimeout...');

    window.setTimeout = function(callback, delay, ...args) {
        // Skip logic only if enabled
        if (window.bcu_skip_timer_enabled) {
            const numericDelay = parseInt(delay, 10);
            if (!isNaN(numericDelay) && numericDelay >= TARGET_MIN && numericDelay <= TARGET_MAX) {
                const now = Date.now();
                if (now - lastSkipTime > SKIP_COOLDOWN) {
                    let cbCode = '';
                    try {
                        if (callback) {
                            cbCode = callback.toString();
                        }
                    } catch(e) {}
                    
                    if (cbCode.includes("return new Promise") && cbCode.includes("var t=this,n=arguments")) {
                        console.log(`[Overlay] Skipped TARGET 3000ms timer.`);
                        lastSkipTime = now;
                        return originalSetTimeout(callback, 0, ...args);
                    }
                }
            }
        }
        return originalSetTimeout(callback, delay, ...args);
    };
    
    window.setSkipTimerEnabled = function(enabled) {
        window.bcu_skip_timer_enabled = enabled;
        console.log('[BCU] Skip Timer Enabled:', enabled);
    };

    // 2. 투명 배경 및 커스텀 CSS 강제 적용 - DOM 로드 대기 후 실행
    function injectCustomCSS() {
        if (document.head) {
            const style = document.createElement('style');
            style.innerHTML = `
                * {
                    margin: 0 !important;
                    padding: 0 !important;
                    box-sizing: border-box !important;
                }

                html, body {
                    line-height: 0 !important;
                    width: 100%;
                    height: 100%;
                    overflow: hidden;
                    background-color: rgba(0, 0, 0, 0) !important;
                }

                [class*="overlay_donation_alarm"] {
                    position: relative !important;
                    width: 1280px;
                    height: 1254px;
                    overflow: hidden !important;
                }

                [class*="overlay_donation_alarm"] * {
                    pointer-events: none;
                }

                /* 기본(가로) 모드: 영상은 상단 1280x720 유지 (혹은 화면 꽉 차게 하려면 수정 필요하지만, 기존 유지) */
                iframe[src*="youtube.com"],
                iframe[src*="youtube-nocookie.com"],
                iframe[src*="/embed-clip-donation/"],
                iframe#chzzk_player {
                    display: block !important; /* 추가 */
                    position: absolute !important;
                    top: 0 !important;
                    left: 0 !important;
                    width: 1280px !important;
                    height: 720px !important;
                    border: none !important;
                    box-shadow: none !important;
                    outline: none !important;
                    pointer-events: auto !important;
                    display: block !important;
                    background-color: transparent !important;
                    /* vertical-align: bottom !important; */
                }

                [class*="overlay_donation_contents"] {
                    position: absolute !important;
                    top: 720px !important;
                    left: 0 !important;
                    width: 100% !important;
                    display: flex;
                    flex-direction: column;
                    align-items: center;
                    gap: 0px !important;
                    padding: 10px 30px 20px 30px;
                    overflow: visible !important;
                }

                /* ... 폰트 등 기존 유지 ... */
                [class*="overlay_donation_description"],
                [class*="overlay_donation_video_title"] {
                    width: 100% !important;
                    font-size: 36px !important;
                    line-height: 56px !important;
                    font-weight: bold !important;
                    color: white !important;
                    text-align: center !important;
                    word-break: keep-all;
                }

                [class*="overlay_donation_description"],
                [class*="overlay_donation_text"] span,
                [class*="overlay_donation_video_title"] {
                    text-shadow: 0 0 7px black, 0 0 12px black !important;
                }

                [class*="overlay_donation_icon_cheese"],
                [class*="badge_container"] img,
                [class*="live_chatting_username_icon"] img {
                    height: 48px !important;
                    width: 48px !important;
                    position: relative;
                    top: 2px;
                }

                [class*="live_chatting_username_wrapper"],
                [class*="live_chatting_username_container"] {
                    display: inline-flex !important;
                    align-items: center !important;
                }

                [class*="overlay_donation_description"] {
                    padding: 0 20px !important;
                }

                [class*="overlay_donation_text"] {
                    width: max-content !important;
                    min-width: 100% !important;
                    white-space: nowrap !important;
                    font-size: 40px !important;
                    line-height: 50px !important;
                    font-weight: normal !important;
                    color: white !important;
                    padding: 0px 10px 5px 10px !important;
                    display: flex !important;
                    justify-content: center !important;
                    align-items: center !important;
                }

                [class*="live_chatting_username_wrapper"] {
                    gap: 6px !important;
                    margin-right: 5px !important;
                }

                [class*="overlay_donation_video_title"] {
                    padding: 20px 30px !important;
                }

                [class*="overlay_donation_money"] {
                    margin-right: 5px !important;
                }

                [class*="overlay_donation_description"] {
                    margin-bottom: 5px !important;
                }
                [class*="overlay_donation_wrapper"] {
                    margin-top: 0px !important;
                }

                /* 세로 모드 (Portrait) */
                body.portrait [class*="overlay_donation_alarm"] {
                    /* width는 main과 동일하게 1280 유지, height도 1254 유지 */
                }

                body.portrait iframe[src*="youtube.com"],
                body.portrait iframe[src*="youtube-nocookie.com"],
                body.portrait iframe[src*="/embed-clip-donation/"],
                body.portrait iframe#chzzk_player {
                    width: 576px !important;
                    height: 1024px !important;
                }

                body.portrait [class*="overlay_donation_contents"] {
                    top: 1024px !important; /* 영상 아래에 배치 */
                    width: 576px !important; /* 영상 너비에 맞춤 */
                    overflow: visible !important;
                }

                /* 정렬 클래스 - 1280 너비 기준 */
                body.portrait.align-center iframe,
                body.portrait.align-center #chzzk_player,
                body.portrait.align-center [class*="overlay_donation_contents"] {
                   position: absolute !important;
                   left: 352px !important; /* (1280-576)/2 */
                   margin: 0 !important;
                   transform: none !important;
                }
                body.portrait.align-left iframe,
                body.portrait.align-left #chzzk_player,
                body.portrait.align-left [class*="overlay_donation_contents"] {
                   position: absolute !important;
                   left: 0px !important;
                   margin: 0 !important;
                   transform: none !important;
                }
                body.portrait.align-right iframe,
                body.portrait.align-right #chzzk_player,
                body.portrait.align-right [class*="overlay_donation_contents"] {
                   position: absolute !important;
                   left: 704px !important; /* 1280-576 */
                   margin: 0 !important;
                   transform: none !important;
                }
            `;
            document.head.appendChild(style);
            console.log('[Overlay] Custom CSS Injected.');
        } else {
            // 아직 head가 없으면 다음 프레임에 재시도
            requestAnimationFrame(injectCustomCSS);
        }
    }
    injectCustomCSS();
    
    // 혹시 모를 상황 대비 window loaded 이벤트에도 추가
    window.addEventListener('load', injectCustomCSS);
})();


function toggleOrientation(isPortrait) {
    if (isPortrait) {
        document.body.classList.add('portrait');
    } else {
        document.body.classList.remove('portrait');
    }
}

// Global storage for portrait dimensions (used by setAlignment to regenerate CSS)
var _bcuPortraitWidth = 576;
var _bcuPortraitHeight = 1024;
var _bcuIncludeText = true;  // 후원텍스트 포함 여부
var _bcuVerticalAlign = 'center'; // Vertical Alignment State

// 후원텍스트 포함 여부 설정 함수
function setIncludeText(includeText) {
    _bcuIncludeText = includeText;
    console.log('[BCU] Include text set to:', includeText);
    // CSS 재생성
    setPortraitSize(_bcuPortraitWidth, _bcuPortraitHeight);
}

function setAlignment(alignment) {
    // Remove old alignment classes
    document.body.classList.remove('align-left', 'align-center', 'align-right');
    document.body.classList.remove('align-v-top', 'align-v-center', 'align-v-bottom');
    
    // Parse 9-grid alignment (e.g., "top-left", "center-center", "bottom-right")
    var parts = alignment.split('-');
    var vAlign = 'top';
    var hAlign = 'center';
    
    if (parts.length >= 2) {
        vAlign = parts[0];  // top, center, bottom
        hAlign = parts[1];  // left, center, right
    } else {
        // Legacy single value (left, center, right)
        hAlign = alignment;
        
        if (alignment === 'top' || alignment === 'bottom') {
            vAlign = alignment;
            hAlign = 'center';
        }
    }
    
    _bcuVerticalAlign = vAlign; // Store for setPortraitSize logic
    
    document.body.classList.add('align-' + hAlign);
    document.body.classList.add('align-v-' + vAlign);
    console.log('[BCU] Alignment set to: h=' + hAlign + ', v=' + vAlign);
    
    // Regenerate CSS with new alignment (uses cached dimensions)
    setPortraitSize(_bcuPortraitWidth, _bcuPortraitHeight);
}

// 세로 모드 크기 설정 함수 (동적 CSS 업데이트)
function setPortraitSize(width, height) {
    // height가 제공되지 않으면 기본 비율로 계산
    if (!height) {
        height = Math.round(width * 1024 / 576);
    }
    
    // Cache dimensions globally for setAlignment to use
    _bcuPortraitWidth = width;
    _bcuPortraitHeight = height;
    var centerOffset = Math.round((1280 - width) / 2);
    var rightOffset = 1280 - width;
    
    // 가로화면 높이: include_text에 따라 다름
    // true: 720(영상) + 162(텍스트) = 882
    // Physical text height (Always 162)
    var textH = 162;
    
    // Alignment text height
    // Rule: Include Text toggle only active if v=center. Else always Included.
    var effectiveInclude = _bcuIncludeText;
    if (_bcuVerticalAlign !== 'center') {
        effectiveInclude = true; 
    }
    var alignTextH = effectiveInclude ? 162 : 0;
    
    // Landscape Alignment Height
    var landscapeAlignH = 720 + alignTextH;

    // Window Size Calculations (Always include text)
    var landscapePhysicalH = 720 + 162;
    var portraitPhysicalH = height + 162;
    
    // Use Physical Height for Window Size (maxH)
    var maxH = Math.max(landscapePhysicalH, portraitPhysicalH);
    
    // 기존 동적 스타일 제거
    var existingStyle = document.getElementById('bcu-portrait-size-style');
    if (existingStyle) existingStyle.remove();
    
    // 새 스타일 생성
    var style = document.createElement('style');
    style.id = 'bcu-portrait-size-style';
    style.textContent = `
        /* Portrait Mode Sizing */
        body.portrait iframe[src*="youtube.com"],
        body.portrait iframe[src*="youtube-nocookie.com"],
        body.portrait iframe[src*="/embed-clip-donation/"],
        body.portrait iframe#chzzk_player {
            width: ${width}px !important;
            height: ${height}px !important;
        }
        body.portrait [class*="overlay_donation_contents"] {
            top: ${height}px !important;
            width: ${width}px !important;
        }
        body.portrait.align-center iframe,
        body.portrait.align-center #chzzk_player,
        body.portrait.align-center [class*="overlay_donation_contents"] {
            left: ${centerOffset}px !important;
        }
        body.portrait.align-right iframe,
        body.portrait.align-right #chzzk_player,
        body.portrait.align-right [class*="overlay_donation_contents"] {
            left: ${rightOffset}px !important;
        }
        
        /* Portrait mode: 세로영상은 항상 top:0 고정. v_align 적용 안 함. */
        
        /* ========================================= */
        /* LANDSCAPE MODE - Vertical Alignment      */
        /* ========================================= */
        /* Landscape: Video 720 + Text 162 = 882    */
        /* Window height = maxH                      */
        /* Bottom: top = maxH - landscapeH          */
        /* Center: top = (maxH - landscapeH) / 2    */
        
        /* Landscape Mode: Vertical Align Bottom */
        body:not(.portrait).align-v-bottom iframe,
        body:not(.portrait).align-v-bottom #chzzk_player {
            top: ${maxH - landscapeAlignH}px !important;
        }
        body:not(.portrait).align-v-bottom [class*="overlay_donation_contents"] {
            top: ${maxH - landscapeAlignH + 720}px !important;
        }
        
        /* Landscape Mode: Vertical Align Center */
        body:not(.portrait).align-v-center iframe,
        body:not(.portrait).align-v-center #chzzk_player {
            top: ${(maxH - landscapeAlignH) / 2}px !important;
        }
        body:not(.portrait).align-v-center [class*="overlay_donation_contents"] {
            top: ${(maxH - landscapeAlignH) / 2 + 720}px !important;
        }
    `;
    document.head.appendChild(style);
    console.log('[BCU] Portrait size set to:', width, 'x', height, 'maxH:', maxH);
}

// 후원알림 텍스트 표시/숨기기 함수
function setDonationTextVisible(visible) {
    var existingStyle = document.getElementById('bcu-donation-text-style');
    if (existingStyle) existingStyle.remove();
    
    if (!visible) {
        var style = document.createElement('style');
        style.id = 'bcu-donation-text-style';
        style.textContent = `
            [class*="overlay_donation_contents"] {
                display: none !important;
            }
        `;
        document.head.appendChild(style);
        console.log('[BCU] Donation text hidden');
    } else {
        console.log('[BCU] Donation text shown');
    }
}

// 3. Video monitoring
(function() {
    function startMonitoring() {
        const targetNode = document.getElementById('root');
        if (!targetNode) {
            setTimeout(startMonitoring, 500);
            return;
        }

        let isVideoPlaying = false;
        let lastVideoSrc = "";

        const config = { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] };

        const callback = function(mutationsList, observer) {
            const iframe = targetNode.querySelector('iframe');
            const currentPlayingState = !!iframe;
            const currentSrc = iframe ? iframe.src : "";

            if (currentPlayingState !== isVideoPlaying || (currentPlayingState && currentSrc !== lastVideoSrc)) {
                if (currentPlayingState) {
                    if (currentSrc !== lastVideoSrc) {
                         console.log("유튜브 영상 재생 시작됨. 영상 주소:", currentSrc);
                         lastVideoSrc = currentSrc;
                    }
                } else {
                    console.log("유튜브 영상 재생 종료됨");
                    lastVideoSrc = "";
                }
                isVideoPlaying = currentPlayingState;
            }
        };

        const observer = new MutationObserver(callback);
        observer.observe(targetNode, config);

        console.log("%c[감지 시작] 치지직 영상후원 상태를 모니터링합니다.");

        setTimeout(() => {
            const iframe = targetNode.querySelector('iframe');
            if (iframe && !isVideoPlaying) {
                 console.log("[Overlay] Autoplay fallback: attempting to click iframe...");
                 iframe.focus();
            }
        }, 3000);
    }

    if (document.readyState === 'loading') {
        window.addEventListener('DOMContentLoaded', startMonitoring);
    } else {
        startMonitoring();
    }
})();

// 5. Chzzk Smart Resolution Detection
(function() {
    let lastResolutionType = null;

    function reportResolution(video) {
        if (!video || video.videoWidth === 0 || video.videoHeight === 0) return;
        
        const ratio = video.videoWidth / video.videoHeight;
        let type = "landscape";
        if (ratio < 0.6) {
            type = "portrait";
        }
        
        if (lastResolutionType !== type) {
            console.log(`[ChzzkResolution] ${type} (${video.videoWidth}x${video.videoHeight})`);
            lastResolutionType = type;
        }
    }

    function setupChzzkObserver() {
        const iframe = document.getElementById('chzzk_player');
        if (!iframe) {
            window._bcuChzzkObserved = false;
            return;
        }
        if (window._bcuChzzkObserved) return;
        
        window._bcuChzzkObserved = true;
        try {
            const innerDoc = iframe.contentWindow.document;
            
            const existingVideo = innerDoc.querySelector('video');
            if (existingVideo) {
                reportResolution(existingVideo);
                existingVideo.addEventListener('loadedmetadata', () => reportResolution(existingVideo));
            }

            const observer = new MutationObserver((mutations) => {
                mutations.forEach((mutation) => {
                    mutation.addedNodes.forEach((node) => {
                        if (node.tagName === 'VIDEO') {
                            reportResolution(node);
                            node.addEventListener('loadedmetadata', () => reportResolution(node));
                        } else if (node.querySelectorAll) {
                            const vids = node.querySelectorAll('video');
                            vids.forEach(v => {
                                reportResolution(v);
                                v.addEventListener('loadedmetadata', () => reportResolution(v));
                            });
                        }
                    });
                });
            });
            const obsTarget = innerDoc.body || innerDoc.documentElement;
            if (obsTarget) {
                observer.observe(obsTarget, { childList: true, subtree: true });
            }
            console.log('[Overlay] Chzzk Iframe Observer Attached');

            iframe.addEventListener('load', () => {
                console.log('[Overlay] Chzzk iframe loaded, re-attaching observer...');
                lastResolutionType = null;
                window._bcuChzzkObserved = false;
                setupChzzkObserver();
            }, { once: true });
            
        } catch(e) {
            window._bcuChzzkObserved = false;
        }
    }

    function init() {
         setupChzzkObserver();
         const mainObserver = new MutationObserver(() => {
            setupChzzkObserver();
         });
         if (document.body) {
             mainObserver.observe(document.body, { childList: true, subtree: true });
         }
    }

    if (document.readyState === 'loading') {
        window.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    
    setInterval(() => {
        const iframe = document.getElementById('chzzk_player');
        if(iframe) {
             try {
                const v = iframe.contentWindow.document.querySelector('video');
                if(v) reportResolution(v);
             } catch(e){}
        }
    }, 1000);

    // 6. Force Iframe Permissions
    (function() {
        function fixPermissions() {
            const iframes = document.getElementsByTagName('iframe');
            for (let i = 0; i < iframes.length; i++) {
                const iframe = iframes[i];
                if (iframe.src && (iframe.src.includes('youtube.com') || iframe.src.includes('youtu.be') || iframe.id === 'chzzk_player')) {
                    const currentAllow = iframe.getAttribute('allow') || "";
                    const required = "autoplay; encrypted-media; clipboard-write; picture-in-picture";
                    
                    if (iframe.src && iframe.src.includes('//m.youtube.com')) {
                        console.log(`[Overlay] Detected Mobile YouTube URL. Forcing PC version...`);
                        iframe.src = iframe.src.replace('//m.youtube.com', '//www.youtube.com');
                        return;
                    }

                    if (!currentAllow.includes('encrypted-media')) {
                        console.log(`[Overlay] Fixing permissions for ${iframe.src}`);
                        let newAllow = currentAllow;
                        if (newAllow) newAllow += "; ";
                        newAllow += required;
                        iframe.setAttribute('allow', newAllow);
                    }
                }
            }
        }

        fixPermissions();

        const observer = new MutationObserver((mutations) => {
             mutations.forEach((m) => {
                 m.addedNodes.forEach((n) => {
                     if (n.tagName === 'IFRAME') {
                         fixPermissions();
                     } else if (n.getElementsByTagName) {
                         if (n.getElementsByTagName('iframe').length > 0) fixPermissions();
                     }
                 });
             });
        });
        const obsTarget = document.body || document.documentElement;
        if (obsTarget) {
            observer.observe(obsTarget, { childList: true, subtree: true });
        }
        
        setInterval(fixPermissions, 2000);
    })();
})();

// 7. BCU Video End Control - 영상 종료 제어 기능
(function() {
    // 영상 강제 종료 함수 (재생바를 맨 뒤로 이동 - 기존 방식)
    window.bcuForceVideoEnd = function() {
        console.log('[BCU] Force video end triggered (seek to end)');
        
        // 1. YouTube iframe 찾기
        const ytIframe = document.querySelector('iframe[src*="youtube.com"], iframe[src*="youtube-nocookie.com"]');
        if (ytIframe) {
            try {
                ytIframe.contentWindow.postMessage(JSON.stringify({
                    event: 'command',
                    func: 'seekTo',
                    args: [99999, true]
                }), '*');
            } catch(e) { console.log('[BCU] postMessage error:', e); }
        }
        
        // 2. Chzzk 클립 iframe 찾기
        const chzzkIframe = document.getElementById('chzzk_player');
        if (chzzkIframe) {
            try {
                const video = chzzkIframe.contentWindow.document.querySelector('video');
                if (video) {
                    video.currentTime = video.duration || 99999;
                }
            } catch(e) {}
        }
        
        // 3. 직접 video 요소 찾기
        document.querySelectorAll('video').forEach(v => {
            try { v.currentTime = v.duration || 99999; } catch(e) {}
        });
    };
    
    // 강제 스킵 함수 (4개 API 순차 호출)
    window.bcuForceSkip = async function() {
        console.log('[BCU] Force skip triggered (4 API calls)');
        
        try {
            const url = window.location.href;
            const channelMatch = url.match(/video-donation\\/([^?]+)/);
            const channelId = channelMatch ? channelMatch[1] : null;
            const donationId = window._bcuCurrentDonationId;
            const videoId = window._bcuCurrentVideoId || 'unknown';
            const payAmount = window._bcuCurrentPayAmount || 0;
            const videoType = window._bcuCurrentVideoType || 'YOUTUBE';
            
            if (!channelId || !donationId) {
                console.log('[BCU] No channelId or donationId found. channelId:', channelId, 'donationId:', donationId);
                return;
            }
            
            const neloHeaders = {
                'Accept': 'application/json, text/plain, */*',
                'Content-Type': 'application/json',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'cross-site'
            };
            
            const neloBaseBody = {
                projectName: 'P519917_glive-fe-pc',
                projectVersion: '0.0.1',
                txtToken: '4d82d7d6ac304ac39a05387c9ad5d807',
                userAgent: navigator.userAgent.toLowerCase()
            };
            
            // 1. VIDEO_DONATION_STOPPED
            console.log('[BCU] Sending 1/4: VIDEO_DONATION_STOPPED');
            await fetch('https://kr-col-ext.nelo.navercorp.com/_store', {
                method: 'POST',
                headers: neloHeaders,
                referrer: url,
                referrerPolicy: 'unsafe-url',
                body: JSON.stringify({
                    ...neloBaseBody,
                    logLevel: 'DEBUG',
                    body: `VIDEO_DONATION_STOPPED channelId: ${channelId}, donationId: ${donationId}, videoId: ${videoId}, videoType: ${videoType}, playMode: VIDEO_PLAY, tierNo: undefined, payAmount: ${payAmount}, donationId: ${donationId}`
                }),
                mode: 'cors',
                credentials: 'omit'
            }).then(r => r.json()).then(d => console.log('[BCU] 1/4 response:', d)).catch(e => console.log('[BCU] 1/4 error:', e));
            
            // 2. VIDEO_DONATION_VIDEO_PLAYBACK_ENDED
            console.log('[BCU] Sending 2/4: VIDEO_DONATION_VIDEO_PLAYBACK_ENDED');
            await fetch('https://kr-col-ext.nelo.navercorp.com/_store', {
                method: 'POST',
                headers: neloHeaders,
                referrer: url,
                referrerPolicy: 'unsafe-url',
                body: JSON.stringify({
                    ...neloBaseBody,
                    logLevel: 'DEBUG',
                    body: `VIDEO_DONATION_VIDEO_PLAYBACK_ENDED channelId: ${channelId}, donationId: ${donationId}, videoId: ${videoId}, videoType: ${videoType}, playMode: VIDEO_PLAY, tierNo: undefined, payAmount: ${payAmount}`
                }),
                mode: 'cors',
                credentials: 'omit'
            }).then(r => r.json()).then(d => console.log('[BCU] 2/4 response:', d)).catch(e => console.log('[BCU] 2/4 error:', e));
            
            // 3. VIDEO_DONATION_VIDEO_END_ACTION
            console.log('[BCU] Sending 3/4: VIDEO_DONATION_VIDEO_END_ACTION');
            await fetch('https://kr-col-ext.nelo.navercorp.com/_store', {
                method: 'POST',
                headers: neloHeaders,
                referrer: url,
                referrerPolicy: 'unsafe-url',
                body: JSON.stringify({
                    ...neloBaseBody,
                    logLevel: 'INFO',
                    body: `VIDEO_DONATION_VIDEO_END_ACTION channelId: ${channelId}, donationId: ${donationId}, videoId: ${videoId}, videoType: ${videoType}, playMode: VIDEO_PLAY, tierNo: undefined, payAmount: ${payAmount}`
                }),
                mode: 'cors',
                credentials: 'omit'
            }).then(r => r.json()).then(d => console.log('[BCU] 3/4 response:', d)).catch(e => console.log('[BCU] 3/4 error:', e));
            
            // 4. PUT /end API
            console.log('[BCU] Sending 4/4: PUT /end API');
            await fetch(`https://api.chzzk.naver.com/manage/v1/video-session/${channelId}/donations/${donationId}/end`, {
                method: 'PUT',
                headers: {
                    'Accept': 'application/json, text/plain, */*',
                    'Cache-Control': 'no-cache',
                    'Pragma': 'no-cache',
                    'If-Modified-Since': 'Mon, 26 Jul 1997 05:00:00 GMT',
                    'Front-Client-Platform-Type': 'PC',
                    'Front-Client-Product-Type': 'web',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'same-site'
                },
                referrer: url,
                referrerPolicy: 'unsafe-url',
                body: null,
                mode: 'cors',
                credentials: 'include'
            }).then(r => r.json()).then(d => console.log('[BCU] 4/4 response:', d)).catch(e => console.log('[BCU] 4/4 error:', e));
            
            console.log('[BCU] All 4 API calls completed!');
            
        } catch(e) { console.log('[BCU] Force skip API call error:', e); }
    };
    
    // fetch 인터셉터로 donationId, videoId, payAmount 추출
    (function() {
        const origFetch = window.fetch;
        window.fetch = function(url, options) {
            try {
                const urlStr = typeof url === 'string' ? url : (url && url.url ? url.url : String(url));
                
                // /donations/{donationId}/play 패턴에서 donationId 추출
                const donationMatch = urlStr.match(/\/donations\/([^\/]+)\/play/);
                if (donationMatch) {
                    window._bcuCurrentDonationId = donationMatch[1];
                    console.log('[BCU] Captured donationId from fetch:', window._bcuCurrentDonationId);
                }
                
                // NELO 로그에서 videoId, payAmount 추출
                if (urlStr.includes('nelo.navercorp.com') && options && options.body) {
                    try {
                        const bodyStr = typeof options.body === 'string' ? options.body : '';
                        if (bodyStr.includes('VIDEO_DONATION_RECEIVED') || bodyStr.includes('VIDEO_PLAY_MODE_START')) {
                            const videoIdMatch = bodyStr.match(/videoId:\s*([^,]+)/);
                            const payAmountMatch = bodyStr.match(/payAmount:\s*(\d+)/);
                            const donationIdMatch = bodyStr.match(/donationId:\s*([^,]+)/);
                            const videoTypeMatch = bodyStr.match(/videoType:\s*([^,]+)/);
                            
                            if (videoIdMatch) {
                                window._bcuCurrentVideoId = videoIdMatch[1].trim();
                                console.log('[BCU] Captured videoId:', window._bcuCurrentVideoId);
                            }
                            if (payAmountMatch) {
                                window._bcuCurrentPayAmount = parseInt(payAmountMatch[1]);
                                console.log('[BCU] Captured payAmount:', window._bcuCurrentPayAmount);
                            }
                            if (donationIdMatch && !window._bcuCurrentDonationId) {
                                window._bcuCurrentDonationId = donationIdMatch[1].trim();
                                console.log('[BCU] Captured donationId from NELO:', window._bcuCurrentDonationId);
                            }
                            if (videoTypeMatch) {
                                window._bcuCurrentVideoType = videoTypeMatch[1].trim();
                                console.log('[BCU] Captured videoType:', window._bcuCurrentVideoType);
                            }
                        }
                    } catch(e) {}
                }
            } catch(e) {}
            return origFetch.apply(this, arguments);
        };
    })();
    
    // XMLHttpRequest 인터셉터 (백업)
    (function() {
        const origOpen = XMLHttpRequest.prototype.open;
        XMLHttpRequest.prototype.open = function(method, url) {
            try {
                const urlStr = typeof url === 'string' ? url : String(url);
                const donationMatch = urlStr.match(/\/donations\/([^\/]+)\/play/);
                if (donationMatch) {
                    window._bcuCurrentDonationId = donationMatch[1];
                    console.log('[BCU] Captured donationId from XHR:', window._bcuCurrentDonationId);
                }
            } catch(e) {}
            return origOpen.apply(this, arguments);
        };
    })();
})();

"""


class SignalBridge(QObject):
    """Bridge for thread-safe signal emission"""
    video_started = pyqtSignal(str)
    resolution_detected = pyqtSignal(str)
    close_requested = pyqtSignal()
    set_volume_requested = pyqtSignal(int)
    set_orientation_requested = pyqtSignal(bool)
    set_alignment_requested = pyqtSignal(str)
    simulate_click_requested = pyqtSignal(int, int)
    simulate_skip_requested = pyqtSignal()
    simulate_key_requested = pyqtSignal(str)
    force_connect_requested = pyqtSignal()
    force_skip_requested = pyqtSignal()
    seek_to_start_requested = pyqtSignal()
    toggle_play_pause_requested = pyqtSignal()
    refresh_page_requested = pyqtSignal(str, bool)
    move_window_requested = pyqtSignal(int, int)
    set_taskbar_visible_requested = pyqtSignal(bool)
    get_position_requested = pyqtSignal()
    set_portrait_size_requested = pyqtSignal(int, int)  # width, height
    set_skip_timer_enabled_requested = pyqtSignal(bool)
    set_include_text_requested = pyqtSignal(bool)



class OverlayWebPage(QWebEnginePage):
    video_started_signal = pyqtSignal(str)
    resolution_detected_signal = pyqtSignal(str)

    def __init__(self, profile, parent=None):
        super().__init__(profile, parent)
        from app.overlay.overlay_logger import get_logger
        self.logger = get_logger("overlay.console")

    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        # Log level mapping
        level_names = {0: "INFO", 1: "WARNING", 2: "ERROR"}
        level_name = level_names.get(level, "DEBUG")
        
        # Log to file with timestamp
        log_message = f"[Console:{level_name}] {message}"
        if level == 2:  # Error
            self.logger.error(log_message)
        elif level == 1:  # Warning
            self.logger.warning(log_message)
        else:
            self.logger.info(log_message)
        
        print(f"[Overlay Console] {message}")
        super().javaScriptConsoleMessage(level, message, lineNumber, sourceID)
        if message.startswith("유튜브 영상 재생 시작됨. 영상 주소:"):
            try:
                video_url = message.split("유튜브 영상 재생 시작됨. 영상 주소:")[1].split("?autoplay")[0].strip()
                self.video_started_signal.emit(video_url)
            except IndexError:
                pass
        
        if message.startswith("[ChzzkResolution]"):
            try:
                res_type = message.split("]")[1].strip().split("(")[0].strip()
                self.resolution_detected_signal.emit(res_type)
            except Exception as e:
                print(f"[Overlay Console] Resolution Parse Error: {e}")

    def featurePermissionRequested(self, securityOrigin, feature):
        self.setFeaturePermission(securityOrigin, feature, QWebEnginePage.PermissionPolicy.PermissionGrantedByUser)


class ChzzkOverlayProcess(QMainWindow):
    """ChzzkOverlay running in separate process"""
    
    def __init__(self, url: str, is_ui: bool, alignment: str = "center"):
        super().__init__()
        
        self.url = url
        self.is_ui = is_ui
        self.is_portrait = False
        self.alignment = alignment
        self.include_text = True
        self.allow_close = False  # Close only allowed via IPC command
        
        # Window flags
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        self.setWindowTitle("치지직 영도 오버레이")
        self.setFixedSize(FRAME_WIDTH, FRAME_HEIGHT)
        
        # Profile setup
        self.profile_path = os.path.join(USERPATH, "BCU", "browser_profile")
        os.makedirs(self.profile_path, exist_ok=True)
        self.persistent_profile = QWebEngineProfile("shared_overlay", self)
        self.persistent_profile.setPersistentStoragePath(self.profile_path)
        self.persistent_profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self.persistent_profile.setHttpUserAgent(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0"
        )
        settings = self.persistent_profile.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        settings.setAttribute(QWebEngineSettings.WebAttribute.PluginsEnabled, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.AllowRunningInsecureContent, True)

        # Browser setup
        self.browser = QWebEngineView()
        self.page = OverlayWebPage(self.persistent_profile, self.browser)
        self.browser.setPage(self.page)
        
        self.browser.setStyleSheet("background: transparent;")
        self.browser.page().setBackgroundColor(Qt.GlobalColor.transparent)
        self.browser.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        
        # JS injection
        self.browser.loadFinished.connect(self._on_load_finished)
        
        # Load URL
        full_url = url + "?cookie=true&w=1280&h=720"
        if is_ui:
            full_url += "&ui=true"
        
        self.browser.load(QUrl(full_url))
        self.setCentralWidget(self.browser)
        
        # Position off-screen
        max_x = 0
        for screen in QApplication.screens():
            geo = screen.geometry()
            right = geo.x() + geo.width()
            if right > max_x:
                max_x = right
        
        self.move(max_x, 0)
        print(f"[Overlay] Window positioned at ({max_x}, 0)")
        
        # Shared memory for frame capture
        self.shm = None
        self.setup_shared_memory()
        
        # Frame capture timer
        self.capture_timer = QTimer(self)
        self.capture_timer.timeout.connect(self.capture_frame)
        self.capture_timer.start(16)  # ~60fps

    def _on_load_finished(self, ok):
        if ok:
            print("[Overlay] Page loaded successfully")
            self.browser.page().runJavaScript(INJECT_JS)
            
            # Re-apply settings to ensure valid state after load
            QTimer.singleShot(100, lambda: self.set_alignment(self.alignment))
            QTimer.singleShot(200, lambda: self.set_portrait_size(getattr(self, 'portrait_width', 576), getattr(self, 'portrait_height', 1024)))
            QTimer.singleShot(300, lambda: self.set_include_text(getattr(self, 'include_text', True)))
            
            # 클라이언트에게 로드 완료 알림 (stdout 통해)
            print("page_loaded:true")

    def setup_shared_memory(self):
        try:
            # Try to unlink existing shared memory
            try:
                existing = shared_memory.SharedMemory(name=SHARED_MEMORY_NAME)
                existing.close()
                existing.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
            
            self.shm = shared_memory.SharedMemory(
                name=SHARED_MEMORY_NAME,
                create=True,
                size=FRAME_SIZE + 8  # +8 for metadata (width, height)
            )
            # Initialize with zeros to prevent garbage frames from previous sessions
            self.shm.buf[:] = bytes([0] * (FRAME_SIZE + 8))
            print(f"[Overlay] Shared memory created: {SHARED_MEMORY_NAME}")
        except Exception as e:
            print(f"[Overlay] Shared memory error: {e}")
            self.shm = None

    def capture_frame(self):
        if not self.shm or not self.isVisible():
            return
        
        try:
            pixmap = self.grab()
            image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
            
            ptr = image.bits()
            ptr.setsize(image.sizeInBytes())
            
            # Write frame data with metadata
            width_bytes = struct.pack('>H', image.width())
            height_bytes = struct.pack('>H', image.height())
            self.shm.buf[0:2] = width_bytes
            self.shm.buf[2:4] = height_bytes
            frame_data = bytes(ptr)
            self.shm.buf[8:8+len(frame_data)] = frame_data
        except Exception as e:
            print(f"[Overlay] Frame capture error: {e}")

    def inject_script(self):
        script = QWebEngineScript()
        script.setSourceCode(INJECT_JS)
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(True)
        
        profile = self.browser.page().profile()
        profile.scripts().insert(script)

    def set_orientation(self, is_portrait: bool):
        self.is_portrait = is_portrait
        print(f"[Overlay] Applying Orientation: {'Portrait' if is_portrait else 'Landscape'}, Align: {self.alignment}")
        
        self.browser.page().runJavaScript(f"toggleOrientation({str(self.is_portrait).lower()});")
        self.browser.page().runJavaScript(f"setAlignment('{self.alignment}');")
        self.browser.setZoomFactor(1.0)

    def set_alignment(self, alignment: str):
        self.alignment = alignment
        self.browser.page().runJavaScript(f"setAlignment('{alignment}');")

    def set_portrait_size(self, width: int, height: int = None):
        """Set portrait mode width and height"""
        if height is None:
            height = int(width * 1024 / 576)
        
        self.portrait_width = width
        self.portrait_height = height
        
        # 윈도우 크기 동적 조절 (가로: 1280, 세로: max(landscape_h, portrait_h))
        # include_text 상태 반영
        text_h = 162 if getattr(self, 'include_text', True) else 0 # legacy var
        
        const_text_h = 162
        landscape_h = 720 + const_text_h
        portrait_h = height + const_text_h
        total_h = max(landscape_h, portrait_h)
        
        # setFixedSize를 사용하여 크기 고정 해제 후 재설정
        self.setFixedSize(1280, total_h)
        
        self.browser.page().runJavaScript(f"setPortraitSize({width}, {height});")
        print(f"[Overlay] Portrait size set to: {width}x{height}, Window resized to: 1280x{total_h}, IncludeText: {getattr(self, 'include_text', True)}")

    def set_include_text(self, include_text: bool):
        """Set include text state and update window size"""
        self.include_text = include_text
        self.browser.page().runJavaScript(f"setIncludeText({str(include_text).lower()});")
        # 크기 재계산
        self.set_portrait_size(getattr(self, 'portrait_width', 576), getattr(self, 'portrait_height', 1024))
        print(f"[Overlay] Include text set to: {include_text}")

    def set_skip_timer_enabled(self, enabled: bool):
        """Enable or disable 3000ms timer skip"""
        self.browser.page().runJavaScript(f"setSkipTimerEnabled({str(enabled).lower()});")
        print(f"[Overlay] Skip timer enabled: {enabled}")


    def refresh_page(self, url: str, is_ui: bool):
        full_url = url + "?cookie=true&w=1280&h=720"
        if is_ui:
            full_url += "&ui=true"
        self.browser.load(QUrl(full_url))

    def simulate_click(self, x: int, y: int):
        target_widget = self.browser.focusProxy()
        if not target_widget:
            target_widget = self.browser

        local_pos = QPointF(x, y)
        
        press_event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            local_pos,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(target_widget, press_event)
        
        release_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            local_pos,
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(target_widget, release_event)
        
        print(f"[Overlay] Native Click Event Sent to ({x}, {y})")

    def simulate_skip(self):
        is_port = self.is_portrait
        align = self.alignment
        
        coords_to_click = []
        
        if not is_port:
            coords_to_click.append((1247, 646))
            coords_to_click.append((1247, 646))
        else:
            if align == 'left':
                coords_to_click.append((542, 950))
                coords_to_click.append((542, 950))
            elif align == 'right':
                coords_to_click.append((1246, 950))
                coords_to_click.append((1246, 950))
            else:
                coords_to_click.append((895, 950))
                coords_to_click.append((895, 950))
        
        print(f"[Overlay] Simulating Skip (Blind Click + Force End) - Mode: {'Portrait' if is_port else 'Landscape'}, Align: {align}")
        for x, y in coords_to_click:
            self.simulate_click(x, y)
        
        self.browser.page().runJavaScript("window.bcuForceVideoEnd && window.bcuForceVideoEnd();")

    def force_connect(self):
        """Trigger force connect (buffer reset)"""
        print("[Overlay] Force Connect Requested")
        self.browser.page().runJavaScript("window.bcuForceConnect && window.bcuForceConnect();")

    def force_skip(self):
        """Trigger force skip (4 API calls)"""
        print("[Overlay] Force Skip Requested")
        self.browser.page().runJavaScript("window.bcuForceSkip && window.bcuForceSkip();")

    def seek_to_start(self):
        """Seek video to start and pause"""
        print("[Overlay] Seek To Start Requested")
        self.browser.page().runJavaScript("window.bcuSeekToStart && window.bcuSeekToStart();")

    def toggle_play_pause(self):
        """Toggle play/pause on video"""
        print("[Overlay] Toggle Play/Pause Requested")
        self.browser.page().runJavaScript("window.bcuTogglePlayPause && window.bcuTogglePlayPause();")

    def simulate_key(self, key: str):
        """Simulate keyboard key press"""
        target_widget = self.browser.focusProxy()
        if not target_widget:
            target_widget = self.browser
        
        # First click to focus
        click_x = 640
        click_y = 360
        if self.is_portrait:
            if self.alignment == 'left':
                click_x = 288
            elif self.alignment == 'right':
                click_x = 992
            click_y = 512
        
        self.simulate_click(click_x, click_y)
        
        # Map key string to Qt key
        key_map = {
            'home': Qt.Key.Key_Home,
            'end': Qt.Key.Key_End,
            'space': Qt.Key.Key_Space,
        }
        
        qt_key = key_map.get(key.lower())
        if qt_key is None:
            print(f"[Overlay] Unknown key: {key}")
            return
        
        def send_key():
            press = QKeyEvent(QEvent.Type.KeyPress, qt_key, Qt.KeyboardModifier.NoModifier)
            release = QKeyEvent(QEvent.Type.KeyRelease, qt_key, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(target_widget, press)
            QApplication.sendEvent(target_widget, release)
            print(f"[Overlay] Key sent: {key}")
        
        # Delay to allow focus
        QTimer.singleShot(100, send_key)

    def set_volume(self, volume: int):
        vol_float = max(0, min(100, volume)) / 100.0
        
        js_code = f"""
        (function() {{
            window.BcuTargetVolume = {vol_float};
            window.BcuTargetVolumeInt = {volume};

            function applyToMedia(el) {{
                if (el && (el.tagName === 'VIDEO' || el.tagName === 'AUDIO')) {{
                    el.volume = window.BcuTargetVolume;
                    if (window.BcuTargetVolume <= 0.01) {{
                        el.muted = true;
                    }} else {{
                        el.muted = false;
                    }}
                }}
            }}

            function applyToIframe(iframe) {{
                if (!iframe || !iframe.contentWindow) return;
                
                try {{
                    if (iframe.contentDocument) {{
                        const doc = iframe.contentDocument;
                        const videos = doc.querySelectorAll('video, audio');
                        videos.forEach(media => applyToMedia(media));
                        const childIframes = doc.querySelectorAll('iframe');
                        childIframes.forEach(child => applyToIframe(child));
                    }}
                }} catch (e) {{}}

                try {{
                    iframe.contentWindow.postMessage(JSON.stringify({{
                        'event': 'command',
                        'func': 'setVolume',
                        'args': [window.BcuTargetVolumeInt]
                    }}), '*');
                }} catch (e) {{}}
            }}

            function applyAll() {{
                document.querySelectorAll('video, audio').forEach(applyToMedia);
                document.querySelectorAll('iframe').forEach(iframe => {{
                    applyToIframe(iframe);
                    iframe.addEventListener('load', () => applyToIframe(iframe));
                    setTimeout(() => applyToIframe(iframe), 1000);
                    setTimeout(() => applyToIframe(iframe), 3600);
                }});
            }}

            applyAll();

            if (!window.BcuVolumeManagerInstalled) {{
                window.BcuVolumeManagerInstalled = true;

                ['play', 'playing', 'loadedmetadata', 'loadeddata', 'durationchange'].forEach(evtName => {{
                    window.addEventListener(evtName, (e) => applyToMedia(e.target), true);
                }});

                const observer = new MutationObserver((mutations) => {{
                    mutations.forEach((mutation) => {{
                        mutation.addedNodes.forEach((node) => {{
                            if (node.nodeType === 1) {{
                                if (node.tagName === 'VIDEO' || node.tagName === 'AUDIO') {{
                                    applyToMedia(node);
                                }} else if (node.tagName === 'IFRAME') {{
                                    applyToIframe(node);
                                    node.addEventListener('load', () => applyToIframe(node));
                                    setTimeout(() => applyToIframe(node), 1000);
                                }} else {{
                                    node.querySelectorAll && node.querySelectorAll('video, audio').forEach(applyToMedia);
                                    node.querySelectorAll && node.querySelectorAll('iframe').forEach(i => {{
                                        applyToIframe(i);
                                        i.addEventListener('load', () => applyToIframe(i));
                                    }});
                                }}
                            }}
                        }});
                    }});
                }});
                
                const obsTarget = document.body || document.documentElement;
                if (obsTarget) {{
                    observer.observe(obsTarget, {{
                        childList: true,
                        subtree: true
                    }});
                }}
            }}
        }})();
        """
        self.browser.page().runJavaScript(js_code)

    def closeEvent(self, event: QCloseEvent):
        # Only allow close if explicitly requested via IPC
        if not self.allow_close:
            event.ignore()
            print("[Overlay] Close event ignored (use IPC command to close)")
            return
        
        self.capture_timer.stop()
        if self.shm:
            try:
                self.shm.close()
                self.shm.unlink()
            except Exception:
                pass
        self.browser.stop()
        self.browser.deleteLater()
        event.accept()
    
    def force_close(self):
        """Close called via IPC - Sets allow_close and closes"""
        self.allow_close = True
        self.close()
    
    def move_to(self, x: int, y: int):
        """Move overlay window to specified position"""
        self.move(x, y)
        print(f"[Overlay] Moved to ({x}, {y})")
    
    def set_taskbar_visible(self, visible: bool):
        """Show or hide overlay from taskbar"""
        current_flags = self.windowFlags()
        if visible:
            # Remove Tool flag to show in taskbar
            new_flags = current_flags & ~Qt.WindowType.Tool
        else:
            # Add Tool flag to hide from taskbar
            new_flags = current_flags | Qt.WindowType.Tool
        
        self.setWindowFlags(new_flags)
        self.show()  # Need to re-show after changing flags
        print(f"[Overlay] Taskbar visible: {visible}")
    
    def get_position(self):
        """Return current window position via IPC"""
        pos = self.pos()
        return pos.x(), pos.y()


class IPCServer:
    """TCP server for receiving commands from main process"""
    
    def __init__(self, overlay: ChzzkOverlayProcess, signal_bridge: SignalBridge):
        self.overlay = overlay
        self.signal_bridge = signal_bridge
        self.server_socket = None
        self.client_socket = None
        self.running = False
        
    def start(self):
        self.running = True
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(('127.0.0.1', IPC_PORT))
        self.server_socket.listen(1)
        self.server_socket.settimeout(1.0)
        
        self.thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.thread.start()
        print(f"[Overlay] IPC Server listening on port {IPC_PORT}")
        
    def _accept_loop(self):
        while self.running:
            try:
                self.client_socket, addr = self.server_socket.accept()
                print(f"[Overlay] Client connected from {addr}")
                self._send_event(evt_ready())
                self._handle_client()
            except socket.timeout:
                continue
            except Exception as e:
                print(f"[Overlay] Accept error: {e}")
                break
    
    def _handle_client(self):
        while self.running and self.client_socket:
            try:
                msg = IPCMessage.read_from_socket(self.client_socket)
                if not msg:
                    break
                self._process_command(msg)
            except Exception as e:
                print(f"[Overlay] Client error: {e}")
                break
        
        if self.client_socket:
            self.client_socket.close()
            self.client_socket = None
        print("[Overlay] Client disconnected")
    
    def _process_command(self, msg: IPCMessage):
        cmd = msg.msg_type
        data = msg.data or {}
        
        if cmd == CommandType.SET_VOLUME.value:
            self.signal_bridge.set_volume_requested.emit(data.get("volume", 50))
        elif cmd == CommandType.SET_ORIENTATION.value:
            self.signal_bridge.set_orientation_requested.emit(data.get("is_portrait", False))
        elif cmd == CommandType.SET_ALIGNMENT.value:
            self.signal_bridge.set_alignment_requested.emit(data.get("alignment", "center"))
        elif cmd == CommandType.SIMULATE_CLICK.value:
            self.signal_bridge.simulate_click_requested.emit(data.get("x", 0), data.get("y", 0))
        elif cmd == CommandType.SIMULATE_SKIP.value:
            self.signal_bridge.simulate_skip_requested.emit()
        elif cmd == CommandType.SIMULATE_KEY.value:
            self.signal_bridge.simulate_key_requested.emit(data.get("key", ""))
        elif cmd == CommandType.FORCE_CONNECT.value:
            self.signal_bridge.force_connect_requested.emit()
        elif cmd == CommandType.FORCE_SKIP.value:
            self.signal_bridge.force_skip_requested.emit()
        elif cmd == CommandType.SEEK_TO_START.value:
            self.signal_bridge.seek_to_start_requested.emit()
        elif cmd == CommandType.TOGGLE_PLAY_PAUSE.value:
            self.signal_bridge.toggle_play_pause_requested.emit()
        elif cmd == CommandType.REFRESH_PAGE.value:
            self.signal_bridge.refresh_page_requested.emit(data.get("url", ""), data.get("is_ui", False))
        elif cmd == CommandType.MOVE_WINDOW.value:
            self.signal_bridge.move_window_requested.emit(data.get("x", 0), data.get("y", 0))
        elif cmd == CommandType.SET_TASKBAR_VISIBLE.value:
            self.signal_bridge.set_taskbar_visible_requested.emit(data.get("visible", True))
        elif cmd == CommandType.GET_POSITION.value:
            self.signal_bridge.get_position_requested.emit()
        elif cmd == CommandType.CLOSE.value:
            self.signal_bridge.close_requested.emit()
        elif cmd == CommandType.SET_PORTRAIT_SIZE.value:
            width = data.get("width", 576)
            height = data.get("height", int(width * 1024 / 576))
            self.signal_bridge.set_portrait_size_requested.emit(width, height)
        elif cmd == CommandType.SET_SKIP_TIMER_ENABLED.value:
            self.signal_bridge.set_skip_timer_enabled_requested.emit(data.get("enabled", True))
        elif cmd == CommandType.SET_INCLUDE_TEXT.value:
            self.signal_bridge.set_include_text_requested.emit(data.get("include_text", True))

        elif cmd == CommandType.PING.value:
            self._send_event(evt_pong())
    
    def _send_event(self, msg: IPCMessage):
        if self.client_socket:
            try:
                self.client_socket.sendall(msg.to_bytes())
            except Exception as e:
                print(f"[Overlay] Send error: {e}")
    
    def send_video_started(self, url: str):
        self._send_event(evt_video_started(url))
    
    def send_resolution_detected(self, res_type: str):
        self._send_event(evt_resolution_detected(res_type))
    
    def send_overlay_closed(self):
        self._send_event(evt_overlay_closed())
    
    def send_position(self, x: int, y: int):
        self._send_event(evt_position_changed(x, y))
    
    def stop(self):
        self.running = False
        if self.client_socket:
            self.client_socket.close()
        if self.server_socket:
            self.server_socket.close()


def main():
    import argparse
    from app.overlay.overlay_logger import setup_overlay_logger
    
    # Setup logger first
    logger = setup_overlay_logger()
    logger.info("="*50)
    logger.info("Overlay Process Starting")
    logger.info("="*50)
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', required=True, help='Overlay URL')
    parser.add_argument('--ui', action='store_true', help='Enable UI mode')
    parser.add_argument('--alignment', default='center', help='Alignment (left/center/right)')
    parser.add_argument('--remote-debugging-port', help='Remote debugging port')
    parser.add_argument('--disable-gpu', action='store_true', help='Disable GPU acceleration')
    args, unknown = parser.parse_known_args()
    
    logger.info(f"URL: {args.url}")
    logger.info(f"UI Mode: {args.ui}")
    logger.info(f"Alignment: {args.alignment}")
    logger.info(f"Disable GPU: {args.disable_gpu}")
    
    if args.remote_debugging_port:
        os.environ["QTWEBENGINE_REMOTE_DEBUGGING_PORT"] = args.remote_debugging_port
        logger.info(f"Remote debugging port: {args.remote_debugging_port}")

    # Chromium flags
    FLAGS = (
        "--enable-features=ProprietaryCodecs "
        "--ffmpeg-branding=Chrome "
        "--disable-background-timer-throttling "
        "--disable-renderer-backgrounding "
        "--disable-backgrounding-occluded-windows"
    )
    
    if args.remote_debugging_port:
        FLAGS += f" --remote-debugging-port={args.remote_debugging_port}"
    
    # GPU acceleration flags
    if args.disable_gpu:
        # Use ANGLE with software rendering instead of conflicting flags
        FLAGS += " --disable-gpu --disable-gpu-sandbox --in-process-gpu --use-angle=swiftshader"
        logger.info("GPU acceleration disabled - using ANGLE software rendering")
        
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = FLAGS
    logger.info(f"Chromium flags: {FLAGS}")
    
    for flag in FLAGS.split():
        sys.argv.append(flag)
    
    logger.info("Creating QApplication...")
    app = QApplication(sys.argv)
    
    logger.info("Creating ChzzkOverlayProcess...")
    # Create overlay
    overlay = ChzzkOverlayProcess(args.url, args.ui, args.alignment)
    
    # Signal bridge for thread-safe calls
    signal_bridge = SignalBridge()
    signal_bridge.set_volume_requested.connect(overlay.set_volume)
    signal_bridge.set_orientation_requested.connect(overlay.set_orientation)
    signal_bridge.set_alignment_requested.connect(overlay.set_alignment)
    signal_bridge.simulate_click_requested.connect(overlay.simulate_click)
    signal_bridge.simulate_skip_requested.connect(overlay.simulate_skip)
    signal_bridge.simulate_key_requested.connect(overlay.simulate_key)
    signal_bridge.force_connect_requested.connect(overlay.force_connect)
    signal_bridge.force_skip_requested.connect(overlay.force_skip)
    signal_bridge.seek_to_start_requested.connect(overlay.seek_to_start)
    signal_bridge.toggle_play_pause_requested.connect(overlay.toggle_play_pause)
    signal_bridge.refresh_page_requested.connect(overlay.refresh_page)
    signal_bridge.move_window_requested.connect(overlay.move_to)
    signal_bridge.set_taskbar_visible_requested.connect(overlay.set_taskbar_visible)
    
    def on_get_position():
        x, y = overlay.get_position()
        ipc_server.send_position(x, y)
    
    signal_bridge.get_position_requested.connect(on_get_position)
    signal_bridge.close_requested.connect(overlay.force_close)
    signal_bridge.set_portrait_size_requested.connect(overlay.set_portrait_size)
    signal_bridge.set_skip_timer_enabled_requested.connect(overlay.set_skip_timer_enabled)
    signal_bridge.set_include_text_requested.connect(overlay.set_include_text)

    
    # Start IPC server
    ipc_server = IPCServer(overlay, signal_bridge)
    ipc_server.start()
    
    # Connect overlay events to IPC
    overlay.page.video_started_signal.connect(ipc_server.send_video_started)
    overlay.page.resolution_detected_signal.connect(ipc_server.send_resolution_detected)
    
    # Cleanup on close
    def on_close():
        ipc_server.send_overlay_closed()
        ipc_server.stop()
        app.quit()
    
    overlay.destroyed.connect(on_close)
    
    overlay.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
