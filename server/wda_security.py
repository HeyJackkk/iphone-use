"""Private phone-side authentication and reproducible pinned WDA hardening."""
import hashlib
import fcntl
import os
from pathlib import Path
import re
import secrets
import stat

SECURITY_POLICY = 'usb-auth-v1'
PATCH = Path(__file__).resolve().parents[1] / 'tooling/wda-security.patch'


def read_token(path):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077 or info.st_size != 64):
            raise ValueError('WDA authentication file must be a private, owner-only regular file.')
        token = os.read(fd, 65).decode('ascii')
        if not re.fullmatch(r'[a-f0-9]{64}', token):
            raise ValueError('Invalid WDA authentication file; preserve it and investigate.')
        return token
    finally:
        os.close(fd)


def auth_token(state_dir):
    path = Path(state_dir) / 'auth-token'
    descriptor = os.open(str(Path(state_dir) / 'auth.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not path.exists() and not path.is_symlink():
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(secrets.token_hex(32))
        return read_token(path)


def security_digest():
    return hashlib.sha256(SECURITY_POLICY.encode() + PATCH.read_bytes()).hexdigest()


def security_marker(state_dir):
    return {'security_policy': SECURITY_POLICY, 'security_digest': security_digest(),
            'auth_digest': hashlib.sha256(auth_token(state_dir).encode()).hexdigest()}


def control_socket(state_dir, port):
    path = Path(state_dir) / ('control-' + str(port) + '.sock')
    if len(os.fsencode(path)) > 100:
        # macOS limits Unix socket addresses. Long/custom/legacy state paths
        # still work through an owner-only short directory; never adopt a
        # directory or symlink created by another account.
        directory = Path('/tmp') / ('iphone-use-' + str(os.getuid()))
        directory.mkdir(mode=0o700, exist_ok=True)
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('Private control socket directory has unsafe ownership or permissions.')
        identity = hashlib.sha256(os.fsencode(Path(state_dir).resolve()) + str(port).encode()).hexdigest()[:24]
        path = directory / (identity + '.sock')
    return path
