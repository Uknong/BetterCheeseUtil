import os
from datetime import datetime

VERSION = "10.0.4"
BUILDNUMBER = "20260112.0"
GLOBALFONTSIZE = 12
F = "\x0c"
ESC = "\x1b\t"
SEPARATOR = "+" + "-" * 70 + "+"
tilde = "~"
USERPATH = os.path.expanduser(tilde)
AUTH_FILE_PATH = os.path.join(USERPATH, "BCU", "auth.json") # Path to store refresh token
AUTH_REDIRECT_URI = "http://localhost:8080/callback"
USERPATH_SLASH = os.path.expanduser(tilde).replace("\\", "/")
startupTime = datetime.now()