"""
Overlay Logger Module

Provides logging functionality for overlay process debugging.
Logs are saved to %USERPROFILE%/BCU/logs/overlay_*.log files.
"""

import os
import logging
import glob
from datetime import datetime
from logging.handlers import RotatingFileHandler

USERPATH = os.path.expanduser("~")
LOG_DIR = os.path.join(USERPATH, "BCU", "log_overlay")
MAX_LOG_FILES = 10  # Keep only the most recent 10 log files


def setup_overlay_logger(name: str = "overlay") -> logging.Logger:
    """
    Setup and return a logger for the overlay process.
    
    Args:
        name: Logger name (default: "overlay")
        
    Returns:
        Configured logger instance
    """
    # Ensure log directory exists
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # Clean up old log files
    cleanup_old_logs()
    
    # Create logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    
    # Avoid duplicate handlers
    if logger.handlers:
        return logger
    
    # Create timestamp for log file name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(LOG_DIR, f"overlay_{timestamp}.log")
    
    # File handler with rotation
    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=5*1024*1024,  # 5MB
        backupCount=3,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # Formatter
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    logger.info(f"Overlay logger initialized. Log file: {log_file}")
    
    return logger


def cleanup_old_logs():
    """Remove old log files, keeping only the most recent MAX_LOG_FILES."""
    try:
        log_pattern = os.path.join(LOG_DIR, "overlay_*.log*")
        log_files = glob.glob(log_pattern)
        
        if len(log_files) <= MAX_LOG_FILES:
            return
        
        # Sort by modification time (oldest first)
        log_files.sort(key=os.path.getmtime)
        
        # Remove oldest files
        files_to_remove = log_files[:-MAX_LOG_FILES]
        for file_path in files_to_remove:
            try:
                os.remove(file_path)
            except Exception:
                pass
                
    except Exception:
        pass


def get_logger(name: str = "overlay") -> logging.Logger:
    """Get an existing logger or create a new one."""
    return logging.getLogger(name)
