"""Runtime-only wallpaper fix for XiaoZhi Desktop 4.1.3.8 (x64).

The tool never changes files in XiaoZhi's installation directory.  It verifies
the exact hash and original bytes of the target DLL before disabling XiaoZhi's
writes to Windows wallpaper.  Hybrid mode clips XiaoZhi's desktop window to its
organizer areas so Explorer owns wallpaper drawing and input everywhere else.
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import uuid
import winreg


TOOL_VERSION = "2.6.3"
TARGET_EXE = "XZDesktop64.exe"
WINDYNAMIC_PREFIX = "windynamicdesktop"
VIRTUAL_DESKTOP_ROOT = (
    r"Software\Microsoft\Windows\CurrentVersion\Explorer\VirtualDesktops"
)
VIRTUAL_DESKTOPS_ROOT = VIRTUAL_DESKTOP_ROOT + r"\Desktops"
VIRTUAL_DESKTOP_BACKUP_NAME = "virtual-desktop-wallpapers-backup.json"
VIRTUAL_DESKTOP_ENFORCE_SECONDS = 2.0
REFRESH_DEBOUNCE_SECONDS = 0.12
REFRESH_COOLDOWN_SECONDS = 2.0
REFRESH_RETRY_SECONDS = 5.0
EXTERNAL_LAYER_REFRESH_DEBOUNCE_SECONDS = 0.20
STARTUP_SETTLE_SECONDS = 10.0

TH32CS_SNAPPROCESS = 0x00000002
TH32CS_SNAPMODULE = 0x00000008
TH32CS_SNAPMODULE32 = 0x00000010
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_CREATE_THREAD = 0x0002
PROCESS_QUERY_INFORMATION = 0x0400
PAGE_EXECUTE_READWRITE = 0x40
PAGE_READWRITE = 0x04
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
ERROR_NO_MORE_FILES = 18
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102
REPAINT_THREAD_TIMEOUT_MS = 20000

MAX_PATH = 260
MAX_MODULE_NAME32 = 255


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


class MODULEENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("th32ModuleID", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("GlblcntUsage", wintypes.DWORD),
        ("ProccntUsage", wintypes.DWORD),
        ("modBaseAddr", ctypes.POINTER(ctypes.c_ubyte)),
        ("modBaseSize", wintypes.DWORD),
        ("hModule", wintypes.HMODULE),
        ("szModule", wintypes.WCHAR * (MAX_MODULE_NAME32 + 1)),
        ("szExePath", wintypes.WCHAR * MAX_PATH),
    ]


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> "GUID":
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


@dataclass(frozen=True)
class PatchSite:
    name: str
    rva: int
    original: bytes
    patch: bytes = b"\x31\xC0\xC3"  # xor eax,eax; ret (HRESULT success)

    @property
    def original_patch_width(self) -> bytes:
        return self.original[: len(self.patch)]


@dataclass(frozen=True)
class ModuleProfile:
    name: str
    sha256: str
    sites: tuple[PatchSite, ...]


@dataclass(frozen=True)
class ModuleInfo:
    base: int
    size: int
    path: Path


CORE_PROFILE = ModuleProfile(
    "XZWallpaperCore64.dll",
    "4CE45DEF7FB9A9C146ABD325493C522F8D2EFC15BD1B5C82BD834CE18A716946",
    (
        PatchSite(
            "Block XZ_SetWallpaper",
            0x3270,
            bytes.fromhex("40 55 56 57 41 54 41 55 41 56 41 57 48 8D AC 24"),
        ),
        PatchSite(
            "Block XZ_SetWallpaperThread",
            0x40A0,
            bytes.fromhex("48 8B C4 55 41 54 41 55 41 56 41 57 48 8D A8 78"),
        ),
    ),
)

IMPL_PROFILE = ModuleProfile(
    "XZDesktopImpl64.dll",
    "A44E4FAB4B21F68F00C91E8CCAE73B91F17D9EB18A04E8F6CA560ABC9319D08A",
    (
        # 14-byte alignment padding immediately before the SetWallpaper helper.
        # The thunk tail-jumps to DuiLib64!CControlUI::SetBkImage through the IAT.
        PatchSite(
            "Dual-monitor SetBkImage thunk",
            0x340B32,
            b"\xCC" * 14,
            bytes.fromhex("FF 25 A0 1B 3C 00") + b"\xCC" * 8,
        ),
        # Replace the bad first-monitor helper call with a call to the thunk.
        PatchSite(
            "Dual-monitor first image call",
            0x33FFDE,
            bytes.fromhex("E8 ED 40 E8 FF"),
            bytes.fromhex("E8 4F 0B 00 00"),
        ),
    ),
)

DESKTOP_SWITCH_PROFILE = ModuleProfile(
    "XZDesktopImpl64.dll",
    "23E4D014D79639D5C600D16F93017562421ED537054D7EDB88A1112A9925DB12",
    (
        PatchSite(
            "Block virtual-desktop full redraw",
            0x3695A1,
            bytes.fromhex("E8 1A 39 01 00"),
            b"\x90" * 5,
        ),
    ),
)

PROFILES = (CORE_PROFILE, DESKTOP_SWITCH_PROFILE)
PER_MONITOR_SYNC_ENABLED = False

# XiaoZhi 4.1.3.8 background-control layout.  Its own SetWallpaper routine
# collapses a 7680x2160 WDD span into a 3840x2160 cache, so the watcher instead
# gives the verified DuiLib background control the already composed span image.
# Complete DLL hashes and remote entry bytes are checked before any call.
IMPL_4138_SHA256 = DESKTOP_SWITCH_PROFILE.sha256
DUI_4138_SHA256 = (
    "F03187A54BB18CC71369B4A6EB36BCFD6BCD5BA70D7315FB92AA428B64E6515E"
)
DUI_SET_BK_IMAGE_RVA = 0x6DA70
DUI_SET_BK_IMAGE_ENTRY = bytes.fromhex(
    "48 89 5C 24 08 48 89 74 24 10 57 48 83 EC 30 0F"
)
XZ_BACKGROUND_CONTROL_OFFSET = 0xCB0
REMOTE_BACKGROUND_BLOCK_SIZE = 0x1000

# XiaoZhi's image-instruction helper receives a structure containing the image
# path and dimensions.  Two tiny wrappers replace that first argument for the
# two monitor calls, while preserving all other arguments and the helper itself.
HELPER_RVA = 0x340B40
FIRST_HELPER_CALL_RVA = 0x33FF29
SECOND_HELPER_CALL_RVA = 0x340337
FIRST_WRAPPER_RVA = 0x33DEA2
SECOND_WRAPPER_RVA = 0x3429F2
FIRST_POINTER_RVA = 0x33D288
SECOND_POINTER_RVA = 0x33D133
FIRST_CALL_ORIGINAL = bytes.fromhex("E8 12 0C 00 00")
SECOND_CALL_ORIGINAL = bytes.fromhex("E8 04 08 00 00")
CAVE14_ORIGINAL = b"\xCC" * 14
POINTER_ORIGINAL = b"\xCC" * 8
PER_MONITOR_BLOCK_SIZE = 0x1000

# In system-desktop mode XiaoZhi keeps running, but its full-virtual-screen
# custom desktop window is hidden so Explorer receives desktop input directly.
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
RGN_OR = 2
NULLREGION = 1
SIMPLEREGION = 2
COMPLEXREGION = 3
XZ_LOG_START_MARKER = b"PerformExtractSreenInfo rcWorkArea:"
XZ_CREATE_AREA_PATTERN = re.compile(
    rb"Create Area (.*?) : (-?\d+),(-?\d+),(-?\d+),(-?\d+)"
)
XZ_MOVE_AREA_PATTERN = re.compile(
    rb"OnAreaPosChange->x:-?\d+,y:-?\d+,rect(-?\d+),(-?\d+),(-?\d+),(-?\d+)"
)
XZ_ADJUST_AREA_PATTERN = re.compile(
    rb"4\|\|\|PerformAdjustRect->name:(.*?),rcItemAbsolute"
    rb"(-?\d+),(-?\d+),(-?\d+),(-?\d+)"
)
XZ_AREA_POSITION_PATTERN = re.compile(rb"Area\[(.*?)\] Pos:")
XZ_AREA_EVENT_TOKENS = (
    b"|||OnAreaPosChange->",
    b"Create Area ",
    b"Desktop save area config: delete area",
    b"4|||PerformAdjustRect->name:",
)

HRESULT = ctypes.c_long
COINIT_APARTMENTTHREADED = 0x2
CLSCTX_ALL = 0x17
CLSID_DESKTOP_WALLPAPER = GUID.parse("C2CF3110-460E-4FC1-B9D0-8A1C0C9CC4BD")
IID_IDESKTOP_WALLPAPER = GUID.parse("B92B56A9-8B55-4E14-9A89-0199BBB6F93B")


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32FirstW.restype = wintypes.BOOL
kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
kernel32.Process32NextW.restype = wintypes.BOOL
kernel32.Module32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32FirstW.restype = wintypes.BOOL
kernel32.Module32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MODULEENTRY32W)]
kernel32.Module32NextW.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.ReadProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPCVOID,
    wintypes.LPVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    wintypes.LPCVOID,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.VirtualProtectEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.VirtualProtectEx.restype = wintypes.BOOL
kernel32.VirtualAllocEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.DWORD,
    wintypes.DWORD,
]
kernel32.VirtualAllocEx.restype = wintypes.LPVOID
kernel32.VirtualFreeEx.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.DWORD,
]
kernel32.VirtualFreeEx.restype = wintypes.BOOL
kernel32.FlushInstructionCache.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, ctypes.c_size_t]
kernel32.FlushInstructionCache.restype = wintypes.BOOL
kernel32.CreateRemoteThread.argtypes = [
    wintypes.HANDLE,
    wintypes.LPVOID,
    ctypes.c_size_t,
    wintypes.LPVOID,
    wintypes.LPVOID,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.CreateRemoteThread.restype = wintypes.HANDLE
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.GetExitCodeThread.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeThread.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.UpdateWindow.argtypes = [wintypes.HWND]
user32.UpdateWindow.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN]
user32.GetWindowRgn.restype = ctypes.c_int
user32.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
user32.SetWindowRgn.restype = ctypes.c_int
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_size_t),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM

gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
gdi32.CreateRectRgn.restype = wintypes.HRGN
gdi32.CombineRgn.argtypes = [wintypes.HRGN, wintypes.HRGN, wintypes.HRGN, ctypes.c_int]
gdi32.CombineRgn.restype = ctypes.c_int
gdi32.GetRgnBox.argtypes = [wintypes.HRGN, ctypes.POINTER(RECT)]
gdi32.GetRgnBox.restype = ctypes.c_int
gdi32.EqualRgn.argtypes = [wintypes.HRGN, wintypes.HRGN]
gdi32.EqualRgn.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL

ole32 = ctypes.OleDLL("ole32")
ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
ole32.CoInitializeEx.restype = HRESULT
ole32.CoUninitialize.argtypes = []
ole32.CoCreateInstance.argtypes = [
    ctypes.POINTER(GUID),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(GUID),
    ctypes.POINTER(ctypes.c_void_p),
]
ole32.CoCreateInstance.restype = HRESULT
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]


class BypassError(RuntimeError):
    pass


def win_error(action: str) -> BypassError:
    error = ctypes.get_last_error()
    return BypassError(f"{action} failed: {ctypes.WinError(error)}")


def close_handle(handle: int) -> None:
    if handle and handle != INVALID_HANDLE_VALUE:
        kernel32.CloseHandle(handle)


def find_processes(requested_pid: int | None) -> list[int]:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise win_error("CreateToolhelp32Snapshot(processes)")
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise win_error("Process32FirstW")
        matches: list[int] = []
        while True:
            pid = int(entry.th32ProcessID)
            if entry.szExeFile.lower() == TARGET_EXE.lower() and (
                requested_pid is None or requested_pid == pid
            ):
                matches.append(pid)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != ERROR_NO_MORE_FILES:
                    raise win_error("Process32NextW")
                break
        return matches
    finally:
        close_handle(snapshot)


def find_module(pid: int, module_name: str | None = None) -> ModuleInfo | None:
    if module_name is None:
        module_name = CORE_PROFILE.name
    flags = TH32CS_SNAPMODULE | TH32CS_SNAPMODULE32
    snapshot = kernel32.CreateToolhelp32Snapshot(flags, pid)
    if snapshot == INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        if error in (5, 87, 299):
            return None
        raise win_error(f"CreateToolhelp32Snapshot(modules, pid={pid})")
    try:
        entry = MODULEENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Module32FirstW(snapshot, ctypes.byref(entry)):
            error = ctypes.get_last_error()
            if error in (ERROR_NO_MORE_FILES, 299):
                return None
            raise win_error(f"Module32FirstW(pid={pid})")
        while True:
            if entry.szModule.lower() == module_name.lower():
                base = ctypes.cast(entry.modBaseAddr, ctypes.c_void_p).value
                if base is None:
                    raise BypassError(f"pid={pid}: module base address is null")
                return ModuleInfo(int(base), int(entry.modBaseSize), Path(entry.szExePath))
            if not kernel32.Module32NextW(snapshot, ctypes.byref(entry)):
                error = ctypes.get_last_error()
                if error != ERROR_NO_MORE_FILES:
                    raise win_error(f"Module32NextW(pid={pid})")
                return None
    finally:
        close_handle(snapshot)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def validate_target(module: ModuleInfo, profile: ModuleProfile) -> None:
    actual_hash = sha256_file(module.path)
    if actual_hash != profile.sha256:
        raise BypassError(
            f"unsupported {profile.name}: sha256={actual_hash}; "
            f"expected={profile.sha256}. No memory was changed."
        )
    highest_end = max(site.rva + len(site.original) for site in profile.sites)
    if highest_end > module.size:
        raise BypassError(
            f"module image is too small: size=0x{module.size:X}, required=0x{highest_end:X}"
        )


def open_process(pid: int) -> int:
    access = (
        PROCESS_CREATE_THREAD
        | PROCESS_QUERY_INFORMATION
        | PROCESS_VM_OPERATION
        | PROCESS_VM_READ
        | PROCESS_VM_WRITE
    )
    process = kernel32.OpenProcess(access, False, pid)
    if not process:
        raise win_error(f"OpenProcess(pid={pid})")
    return process


def read_memory(process: int, address: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    read = ctypes.c_size_t()
    if not kernel32.ReadProcessMemory(process, address, buffer, size, ctypes.byref(read)):
        raise win_error(f"ReadProcessMemory(0x{address:X})")
    if read.value != size:
        raise BypassError(f"short read at 0x{address:X}: {read.value}/{size} bytes")
    return bytes(buffer.raw)


def write_memory(process: int, address: int, payload: bytes) -> None:
    old_protect = wintypes.DWORD()
    if not kernel32.VirtualProtectEx(
        process, address, len(payload), PAGE_EXECUTE_READWRITE, ctypes.byref(old_protect)
    ):
        raise win_error(f"VirtualProtectEx(0x{address:X}, RWX)")
    try:
        written = ctypes.c_size_t()
        source = ctypes.create_string_buffer(payload)
        if not kernel32.WriteProcessMemory(
            process, address, source, len(payload), ctypes.byref(written)
        ):
            raise win_error(f"WriteProcessMemory(0x{address:X})")
        if written.value != len(payload):
            raise BypassError(
                f"short write at 0x{address:X}: {written.value}/{len(payload)} bytes"
            )
        if not kernel32.FlushInstructionCache(process, address, len(payload)):
            raise win_error(f"FlushInstructionCache(0x{address:X})")
    finally:
        restored = wintypes.DWORD()
        if not kernel32.VirtualProtectEx(
            process, address, len(payload), old_protect.value, ctypes.byref(restored)
        ):
            raise win_error(f"VirtualProtectEx(0x{address:X}, restore)")


def write_data_memory(process: int, address: int, payload: bytes) -> None:
    written = ctypes.c_size_t()
    source = ctypes.create_string_buffer(payload)
    if not kernel32.WriteProcessMemory(
        process, address, source, len(payload), ctypes.byref(written)
    ):
        raise win_error(f"WriteProcessMemory(data 0x{address:X})")
    if written.value != len(payload):
        raise BypassError(
            f"short data write at 0x{address:X}: {written.value}/{len(payload)} bytes"
        )


def relative_call(source_rva: int, target_rva: int) -> bytes:
    return b"\xE8" + struct.pack("<i", target_rva - (source_rva + 5))


def wrapper_bytes(wrapper_rva: int, pointer_rva: int) -> bytes:
    mov = bytes.fromhex("48 8B 0D") + struct.pack(
        "<i", pointer_rva - (wrapper_rva + 7)
    )
    jump = b"\xE9" + struct.pack("<i", HELPER_RVA - (wrapper_rva + 12))
    return mov + jump + b"\xCC\xCC"


FIRST_CALL_PATCH = relative_call(FIRST_HELPER_CALL_RVA, FIRST_WRAPPER_RVA)
SECOND_CALL_PATCH = relative_call(SECOND_HELPER_CALL_RVA, SECOND_WRAPPER_RVA)
FIRST_WRAPPER_PATCH = wrapper_bytes(FIRST_WRAPPER_RVA, FIRST_POINTER_RVA)
SECOND_WRAPPER_PATCH = wrapper_bytes(SECOND_WRAPPER_RVA, SECOND_POINTER_RVA)


def com_check(hr: int, action: str) -> None:
    if hr < 0:
        raise BypassError(f"{action} failed with HRESULT 0x{hr & 0xFFFFFFFF:08X}")


def com_method(instance: ctypes.c_void_p, index: int, *argtypes):
    vtable = ctypes.cast(
        instance, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
    ).contents
    prototype = ctypes.WINFUNCTYPE(HRESULT, ctypes.c_void_p, *argtypes)
    return prototype(vtable[index])


def take_com_string(pointer: ctypes.c_void_p) -> str:
    if not pointer.value:
        return ""
    try:
        return ctypes.wstring_at(pointer.value)
    finally:
        ole32.CoTaskMemFree(pointer)


def query_monitor_wallpapers() -> list[dict]:
    com_check(ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED), "CoInitializeEx")
    instance = ctypes.c_void_p()
    try:
        com_check(
            ole32.CoCreateInstance(
                ctypes.byref(CLSID_DESKTOP_WALLPAPER),
                None,
                CLSCTX_ALL,
                ctypes.byref(IID_IDESKTOP_WALLPAPER),
                ctypes.byref(instance),
            ),
            "CoCreateInstance(IDesktopWallpaper)",
        )
        get_wallpaper = com_method(
            instance, 4, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)
        )
        get_monitor_path = com_method(
            instance, 5, wintypes.UINT, ctypes.POINTER(ctypes.c_void_p)
        )
        get_monitor_count = com_method(instance, 6, ctypes.POINTER(wintypes.UINT))
        get_monitor_rect = com_method(
            instance, 7, wintypes.LPCWSTR, ctypes.POINTER(RECT)
        )
        count = wintypes.UINT()
        com_check(get_monitor_count(instance, ctypes.byref(count)), "GetMonitorDevicePathCount")
        monitors: list[dict] = []
        for index in range(count.value):
            monitor_pointer = ctypes.c_void_p()
            com_check(
                get_monitor_path(instance, index, ctypes.byref(monitor_pointer)),
                "GetMonitorDevicePathAt",
            )
            monitor_id = take_com_string(monitor_pointer)
            wallpaper_pointer = ctypes.c_void_p()
            com_check(
                get_wallpaper(instance, monitor_id, ctypes.byref(wallpaper_pointer)),
                "GetWallpaper",
            )
            rect = RECT()
            com_check(
                get_monitor_rect(instance, monitor_id, ctypes.byref(rect)),
                "GetMonitorRECT",
            )
            monitors.append(
                {
                    "index": index,
                    "rect": [rect.left, rect.top, rect.right, rect.bottom],
                    "wallpaper": take_com_string(wallpaper_pointer),
                }
            )
        com_method(instance, 2)(instance)
        instance = ctypes.c_void_p()
        return monitors
    finally:
        if instance.value:
            try:
                com_method(instance, 2)(instance)
            except Exception:
                pass
        ole32.CoUninitialize()


def image_size(path: str, fallback: tuple[int, int]) -> tuple[int, int]:
    """Read common wallpaper dimensions without adding an image-library dependency."""
    try:
        with Path(path).open("rb") as stream:
            header = stream.read(32)
            if header.startswith(b"\x89PNG\r\n\x1a\n") and len(header) >= 24:
                return struct.unpack(">II", header[16:24])
            if header[:2] == b"BM" and len(header) >= 26:
                width, height = struct.unpack("<ii", header[18:26])
                return abs(width), abs(height)
            if header[:2] == b"\xFF\xD8":
                stream.seek(2)
                while True:
                    byte = stream.read(1)
                    if not byte:
                        break
                    if byte != b"\xFF":
                        continue
                    while byte == b"\xFF":
                        byte = stream.read(1)
                    marker = byte[0]
                    if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                        continue
                    length_bytes = stream.read(2)
                    if len(length_bytes) != 2:
                        break
                    length = struct.unpack(">H", length_bytes)[0]
                    if marker in {
                        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
                    }:
                        data = stream.read(5)
                        if len(data) == 5:
                            height, width = struct.unpack(">HH", data[1:5])
                            return width, height
                        break
                    stream.seek(max(0, length - 2), 1)
    except OSError:
        pass
    return fallback


def build_monitor_struct(
    block: bytearray,
    allocation: int,
    offset: int,
    monitor: dict,
) -> int:
    path = monitor["wallpaper"]
    rect = monitor["rect"]
    fallback = (max(1, rect[2] - rect[0]), max(1, rect[3] - rect[1]))
    width, height = image_size(path, fallback)
    style_address = allocation + offset
    pointer_cell_address = allocation + offset + 0x08
    image_object_address = allocation + offset + 0x20
    wstring_address = allocation + offset + 0x80
    path_address = allocation + offset + 0xC0
    argument_address = allocation + offset + 0x280
    encoded = path.encode("utf-16-le") + b"\x00\x00"
    if len(encoded) > 0x1C0:
        raise BypassError(f"wallpaper path is too long: {path}")
    struct.pack_into("<I", block, offset, 3)  # keep aspect ratio
    struct.pack_into("<Q", block, offset + 0x08, image_object_address)
    struct.pack_into("<II", block, offset + 0x40, width, height)
    struct.pack_into("<Q", block, offset + 0x80, path_address)
    struct.pack_into("<QQ", block, offset + 0x90, len(path), len(path))
    block[offset + 0xC0 : offset + 0xC0 + len(encoded)] = encoded
    struct.pack_into(
        "<QQQ",
        block,
        offset + 0x280,
        style_address,
        pointer_cell_address,
        wstring_address,
    )
    return argument_address


def site_state(current: bytes, site: PatchSite) -> str:
    if current == site.original:
        return "original"
    expected_patched = site.patch + site.original[len(site.patch) :]
    if current == expected_patched:
        return "patched"
    return "mismatch"


def per_monitor_state(process: int, module: ModuleInfo) -> tuple[str, int | None]:
    values = {
        "first_call": read_memory(process, module.base + FIRST_HELPER_CALL_RVA, 5),
        "second_call": read_memory(process, module.base + SECOND_HELPER_CALL_RVA, 5),
        "first_wrapper": read_memory(process, module.base + FIRST_WRAPPER_RVA, 14),
        "second_wrapper": read_memory(process, module.base + SECOND_WRAPPER_RVA, 14),
        "first_pointer": read_memory(process, module.base + FIRST_POINTER_RVA, 8),
        "second_pointer": read_memory(process, module.base + SECOND_POINTER_RVA, 8),
    }
    if (
        values["first_call"] == FIRST_CALL_ORIGINAL
        and values["second_call"] == SECOND_CALL_ORIGINAL
        and values["first_wrapper"] == CAVE14_ORIGINAL
        and values["second_wrapper"] == CAVE14_ORIGINAL
        and values["first_pointer"] == POINTER_ORIGINAL
        and values["second_pointer"] == POINTER_ORIGINAL
    ):
        return "original", None
    if (
        values["first_call"] == FIRST_CALL_PATCH
        and values["second_call"] == SECOND_CALL_PATCH
        and values["first_wrapper"] == FIRST_WRAPPER_PATCH
        and values["second_wrapper"] == SECOND_WRAPPER_PATCH
        and values["first_pointer"] != POINTER_ORIGINAL
        and values["second_pointer"] != POINTER_ORIGINAL
    ):
        first_argument = struct.unpack("<Q", values["first_pointer"])[0]
        second_argument = struct.unpack("<Q", values["second_pointer"])[0]
        allocation = first_argument - 0x280
        if second_argument != allocation + 0x680:
            return "mismatch", None
        return "patched", allocation
    return "mismatch", None


def validate_wallpapers(monitors: list[dict]) -> tuple[dict, dict]:
    if len(monitors) != 2:
        raise BypassError(f"per-monitor sync requires exactly two monitors; found {len(monitors)}")
    for monitor in monitors:
        path = monitor.get("wallpaper", "")
        if not path or not Path(path).is_file():
            raise BypassError(f"Windows returned an unavailable wallpaper path: {path!r}")
    left = min(monitors, key=lambda item: item["rect"][0])
    right = max(monitors, key=lambda item: item["rect"][0])
    return left, right


def sync_per_monitor_paths(
    process: int,
    pid: int,
    module: ModuleInfo,
    monitors: list[dict] | None = None,
) -> dict:
    state, allocation = per_monitor_state(process, module)
    if state == "mismatch":
        raise BypassError("per-monitor path hook has unexpected bytes; no memory was changed")
    if monitors is None:
        monitors = query_monitor_wallpapers()
    left, right = validate_wallpapers(monitors)
    installed_now = state == "original"
    changed: list[tuple[int, bytes]] = []
    if installed_now:
        allocated = kernel32.VirtualAllocEx(
            process,
            None,
            PER_MONITOR_BLOCK_SIZE,
            MEM_COMMIT | MEM_RESERVE,
            PAGE_READWRITE,
        )
        if not allocated:
            raise win_error("VirtualAllocEx(per-monitor data)")
        allocation = int(ctypes.cast(allocated, ctypes.c_void_p).value)
    assert allocation is not None
    try:
        block = bytearray(PER_MONITOR_BLOCK_SIZE)
        # XiaoZhi builds the primary/right rectangle first and the left second.
        first_argument = build_monitor_struct(block, allocation, 0x000, right)
        second_argument = build_monitor_struct(block, allocation, 0x400, left)
        write_data_memory(process, allocation, bytes(block))
        if installed_now:
            patches = (
                (FIRST_POINTER_RVA, struct.pack("<Q", first_argument)),
                (SECOND_POINTER_RVA, struct.pack("<Q", second_argument)),
                (FIRST_WRAPPER_RVA, FIRST_WRAPPER_PATCH),
                (SECOND_WRAPPER_RVA, SECOND_WRAPPER_PATCH),
                (FIRST_HELPER_CALL_RVA, FIRST_CALL_PATCH),
                (SECOND_HELPER_CALL_RVA, SECOND_CALL_PATCH),
            )
            for rva, payload in patches:
                address = module.base + rva
                rollback = read_memory(process, address, len(payload))
                write_memory(process, address, payload)
                changed.append((address, rollback))
        verified, verified_allocation = per_monitor_state(process, module)
        if verified != "patched" or verified_allocation != allocation:
            raise BypassError("per-monitor path hook read-back verification failed")
        repaint = trigger_repaint(process, pid)
        return {
            "state": "patched",
            "operation": "installed" if installed_now else "updated",
            "allocation": f"0x{allocation:X}",
            "left": left["wallpaper"],
            "right": right["wallpaper"],
            "repaint": repaint,
        }
    except Exception:
        if installed_now:
            for address, rollback in reversed(changed):
                try:
                    write_memory(process, address, rollback)
                except Exception:
                    pass
            kernel32.VirtualFreeEx(process, allocation, 0, MEM_RELEASE)
        raise


def restore_per_monitor_paths(process: int, module: ModuleInfo) -> dict:
    state, allocation = per_monitor_state(process, module)
    if state == "original":
        return {"state": "original", "operation": "unchanged"}
    if state == "mismatch" or allocation is None:
        raise BypassError("per-monitor path hook has unexpected bytes; restore refused")
    # Disconnect the call sites before removing wrappers, pointers, and data.
    restores = (
        (FIRST_HELPER_CALL_RVA, FIRST_CALL_ORIGINAL),
        (SECOND_HELPER_CALL_RVA, SECOND_CALL_ORIGINAL),
        (FIRST_WRAPPER_RVA, CAVE14_ORIGINAL),
        (SECOND_WRAPPER_RVA, CAVE14_ORIGINAL),
        (FIRST_POINTER_RVA, POINTER_ORIGINAL),
        (SECOND_POINTER_RVA, POINTER_ORIGINAL),
    )
    for rva, payload in restores:
        write_memory(process, module.base + rva, payload)
    if per_monitor_state(process, module)[0] != "original":
        raise BypassError("per-monitor path hook restore verification failed")
    if not kernel32.VirtualFreeEx(process, allocation, 0, MEM_RELEASE):
        raise win_error("VirtualFreeEx(per-monitor data)")
    return {"state": "original", "operation": "restored"}


def resolve_modules(pid: int) -> list[tuple[ModuleProfile, ModuleInfo]]:
    resolved: list[tuple[ModuleProfile, ModuleInfo]] = []
    for profile in PROFILES:
        module = find_module(pid, profile.name)
        if module is None:
            raise BypassError(f"pid={pid}: {profile.name} is not loaded yet")
        validate_target(module, profile)
        resolved.append((profile, module))
    return resolved


def find_xz_window(pid: int) -> tuple[int, int] | None:
    result: tuple[int, int] | None = None

    def inspect(hwnd: int) -> bool:
        nonlocal result
        class_name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        if class_name.value != "XZDesktopWnd":
            return False
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if int(window_pid.value) != pid:
            return False
        userdata = int(user32.GetWindowLongPtrW(hwnd, -21))
        if userdata:
            result = (int(hwnd), userdata)
            return True
        return False

    @WNDENUMPROC
    def child_callback(hwnd: int, _lparam: int) -> bool:
        return not inspect(hwnd)

    @WNDENUMPROC
    def top_callback(hwnd: int, _lparam: int) -> bool:
        if inspect(hwnd):
            return False
        user32.EnumChildWindows(hwnd, child_callback, 0)
        return result is None

    user32.EnumWindows(top_callback, 0)
    return result


_AREA_LOG_CACHE_KEY: tuple | None = None
_AREA_LOG_CACHE_VALUE: tuple[Path, list[tuple[int, int, int, int]]] | None = None


def _valid_area_rectangle(
    rect: tuple[int, int, int, int],
    width: int,
    height: int,
) -> bool:
    left, top, right, bottom = rect
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        return False
    # OnAreaPosChange also logs the root desktop surface.  It is not a user
    # organizer and must never be included in hybrid mode.
    return (right - left) < width * 0.95 or (bottom - top) < height * 0.95


def _rectangle_cost(
    old: tuple[int, int, int, int],
    new: tuple[int, int, int, int],
) -> int:
    old_width, old_height = old[2] - old[0], old[3] - old[1]
    new_width, new_height = new[2] - new[0], new[3] - new[1]
    size_delta = abs(old_width - new_width) + abs(old_height - new_height)
    center_delta = abs((old[0] + old[2]) - (new[0] + new[2]))
    center_delta += abs((old[1] + old[3]) - (new[1] + new[3]))
    return size_delta * 4 + center_delta


def latest_area_rectangles(
    width: int,
    height: int,
) -> tuple[Path, list[tuple[int, int, int, int]]]:
    global _AREA_LOG_CACHE_KEY, _AREA_LOG_CACHE_VALUE
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        raise BypassError("LOCALAPPDATA is unavailable; cannot locate XiaoZhi area logs")
    log_root = Path(local_appdata) / "XZDesktop" / "crash" / "log"
    try:
        logs = sorted(
            log_root.glob("XZDesktopImpl_*.log"),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
    except OSError as exc:
        raise BypassError(f"cannot enumerate XiaoZhi area logs: {exc}") from exc
    if not logs:
        raise BypassError(f"XiaoZhi area log was not found under {log_root}")
    log = logs[0]
    stat = log.stat()
    cache_key = (str(log), stat.st_mtime_ns, stat.st_size, width, height)
    if cache_key == _AREA_LOG_CACHE_KEY and _AREA_LOG_CACHE_VALUE is not None:
        cached_log, cached_rectangles = _AREA_LOG_CACHE_VALUE
        return cached_log, list(cached_rectangles)

    data = log.read_bytes()
    marker_positions = [
        match.start() for match in re.finditer(re.escape(XZ_LOG_START_MARKER), data)
    ]
    if not marker_positions:
        raise BypassError(f"latest desktop-start marker was not found in {log}")

    # XiaoZhi 4.1.3.8 emits PerformExtractSreenInfo on every resize frame, not
    # just at process startup.  A real full desktop snapshot is distinguished
    # by the root XZDesktopWnd OnAreaPosChange event that follows it.  Start at
    # the newest such snapshot, then merge every later incremental area move.
    root_move = re.compile(
        rb"OnAreaPosChange->x:0,y:0,rect0,0,"
        + str(width).encode("ascii")
        + rb","
        + str(height).encode("ascii")
        + rb"(?:\D|$)"
    )
    start: int | None = None
    for index in range(len(marker_positions) - 1, -1, -1):
        segment_start = marker_positions[index]
        segment_end = (
            marker_positions[index + 1]
            if index + 1 < len(marker_positions)
            else len(data)
        )
        if root_move.search(data[segment_start:segment_end]):
            start = segment_start
            break
    if start is None:
        # Compatibility fallback for older logs that do not emit the root
        # window event.  This is no worse than the pre-2.4.1 behavior.
        start = marker_positions[-1]

    named_rectangles: dict[bytes, tuple[int, int, int, int]] = {}
    unnamed_rectangles: list[tuple[int, int, int, int]] = []
    active_name: bytes | None = None
    last_touched_name: bytes | None = None
    last_touched_unnamed: int | None = None
    for line in data[start:].splitlines():
        position_match = XZ_AREA_POSITION_PATTERN.search(line)
        if position_match is not None:
            active_name = position_match.group(1)

        create_match = XZ_CREATE_AREA_PATTERN.search(line)
        if create_match is not None:
            name = create_match.group(1)
            rect = tuple(int(value) for value in create_match.groups()[1:])
            active_name = name
            last_touched_name = name
            last_touched_unnamed = None
            if _valid_area_rectangle(rect, width, height):
                named_rectangles[name] = rect
            else:
                named_rectangles.pop(name, None)
            continue

        adjust_match = XZ_ADJUST_AREA_PATTERN.search(line)
        if adjust_match is not None:
            name = adjust_match.group(1)
            rect = tuple(int(value) for value in adjust_match.groups()[1:])
            active_name = name
            if _valid_area_rectangle(rect, width, height):
                named_rectangles[name] = rect
            else:
                named_rectangles.pop(name, None)
            continue

        move_match = XZ_MOVE_AREA_PATTERN.search(line)
        if move_match is not None:
            rect = tuple(int(value) for value in move_match.groups())
            if not _valid_area_rectangle(rect, width, height):
                continue
            exact_name = next(
                (name for name, old_rect in named_rectangles.items() if old_rect == rect),
                None,
            )
            if exact_name is not None:
                last_touched_name = exact_name
                active_name = exact_name
                last_touched_unnamed = None
            elif named_rectangles:
                closest_name = min(
                    named_rectangles,
                    key=lambda name: _rectangle_cost(named_rectangles[name], rect),
                )
                named_rectangles[closest_name] = rect
                last_touched_name = closest_name
                active_name = closest_name
                last_touched_unnamed = None
            elif active_name is not None:
                named_rectangles[active_name] = rect
                last_touched_name = active_name
                last_touched_unnamed = None
            elif rect in unnamed_rectangles:
                last_touched_unnamed = unnamed_rectangles.index(rect)
                last_touched_name = None
            elif not unnamed_rectangles:
                unnamed_rectangles.append(rect)
                last_touched_unnamed = len(unnamed_rectangles) - 1
                last_touched_name = None
            else:
                last_touched_unnamed = min(
                    range(len(unnamed_rectangles)),
                    key=lambda index: _rectangle_cost(unnamed_rectangles[index], rect),
                )
                unnamed_rectangles[last_touched_unnamed] = rect
                last_touched_name = None
            continue

        if b"Desktop save area config: delete area" in line:
            if last_touched_name is not None:
                named_rectangles.pop(last_touched_name, None)
                if active_name == last_touched_name:
                    active_name = None
                last_touched_name = None
            elif last_touched_unnamed is not None:
                unnamed_rectangles.pop(last_touched_unnamed)
                last_touched_unnamed = None

    rectangles = sorted(
        set(named_rectangles.values()) | set(unnamed_rectangles),
        key=lambda rect: (rect[1], rect[0], rect[3], rect[2]),
    )
    if not rectangles:
        raise BypassError(f"no live organizer rectangles were found in {log}")
    _AREA_LOG_CACHE_KEY = cache_key
    _AREA_LOG_CACHE_VALUE = (log, list(rectangles))
    return log, rectangles


def window_region_state(hwnd: int) -> dict:
    region = gdi32.CreateRectRgn(0, 0, 0, 0)
    if not region:
        raise win_error("CreateRectRgn(region state)")
    try:
        kind = user32.GetWindowRgn(hwnd, region)
        if kind == 0:
            return {"kind": "none"}
        box = RECT()
        gdi32.GetRgnBox(region, ctypes.byref(box))
        kind_names = {
            NULLREGION: "null",
            SIMPLEREGION: "simple",
            COMPLEXREGION: "complex",
        }
        return {
            "kind": kind_names.get(kind, str(kind)),
            "box": [box.left, box.top, box.right, box.bottom],
        }
    finally:
        gdi32.DeleteObject(region)


def clear_window_region(hwnd: int) -> str:
    if window_region_state(hwnd)["kind"] == "none":
        return "unchanged"
    if not user32.SetWindowRgn(hwnd, None, True):
        raise win_error("SetWindowRgn(clear)")
    if window_region_state(hwnd)["kind"] != "none":
        raise BypassError("XZDesktopWnd region clear verification failed")
    return "cleared"


def apply_window_region(
    hwnd: int,
    rectangles: list[tuple[int, int, int, int]],
) -> str:
    target = gdi32.CreateRectRgn(0, 0, 0, 0)
    if not target:
        raise win_error("CreateRectRgn(target)")
    transferred = False
    current = None
    try:
        for rect in rectangles:
            part = gdi32.CreateRectRgn(*rect)
            if not part:
                raise win_error("CreateRectRgn(area)")
            try:
                if gdi32.CombineRgn(target, target, part, RGN_OR) == 0:
                    raise win_error("CombineRgn(area)")
            finally:
                gdi32.DeleteObject(part)

        current = gdi32.CreateRectRgn(0, 0, 0, 0)
        if not current:
            raise win_error("CreateRectRgn(current)")
        current_kind = user32.GetWindowRgn(hwnd, current)
        if current_kind != 0 and gdi32.EqualRgn(current, target):
            return "unchanged"
        if not user32.SetWindowRgn(hwnd, target, True):
            raise win_error("SetWindowRgn(hybrid)")
        transferred = True
        return "applied"
    finally:
        if current:
            gdi32.DeleteObject(current)
        if not transferred:
            gdi32.DeleteObject(target)


def desktop_layer_state(pid: int) -> dict:
    window = find_xz_window(pid)
    if window is None:
        return {"state": "window_not_ready"}
    hwnd, _this_ptr = window
    visible = bool(user32.IsWindowVisible(hwnd))
    region = window_region_state(hwnd)
    if not visible:
        state = "hidden_system"
    elif region["kind"] == "none":
        state = "visible_xiaozhi"
    else:
        state = "visible_hybrid"
    return {
        "state": state,
        "hwnd": f"0x{hwnd:X}",
        "visible": visible,
        "region": region,
    }


def set_desktop_layer(pid: int, mode: str) -> dict:
    if mode not in ("hybrid", "system", "xz"):
        raise BypassError(f"unsupported desktop mode: {mode}")
    window = find_xz_window(pid)
    if window is None:
        return {"state": "window_not_ready", "requested_mode": mode}
    hwnd, _this_ptr = window
    before_visible = bool(user32.IsWindowVisible(hwnd))
    area_source = None
    rectangles: list[tuple[int, int, int, int]] | None = None

    if mode == "system":
        user32.ShowWindow(hwnd, SW_HIDE)
        region_operation = clear_window_region(hwnd)
    elif mode == "xz":
        region_operation = clear_window_region(hwnd)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        user32.UpdateWindow(hwnd)
    else:
        window_rect = RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(window_rect)):
            raise win_error("GetWindowRect(XZDesktopWnd)")
        width = window_rect.right - window_rect.left
        height = window_rect.bottom - window_rect.top
        area_source, rectangles = latest_area_rectangles(width, height)
        region_operation = apply_window_region(hwnd, rectangles)
        user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        user32.UpdateWindow(hwnd)

    after_visible = bool(user32.IsWindowVisible(hwnd))
    expected_visible = mode != "system"
    if after_visible != expected_visible:
        raise BypassError(
            f"pid={pid}: XZDesktopWnd visibility did not change to desktop mode {mode}"
        )
    result = desktop_layer_state(pid)
    visibility_operation = (
        "unchanged"
        if before_visible == after_visible
        else ("shown" if after_visible else "hidden")
    )
    result.update(
        {
            "operation": f"visibility_{visibility_operation};region_{region_operation}",
            "requested_mode": mode,
        }
    )
    if area_source is not None:
        result["area_source"] = str(area_source)
    if rectangles is not None:
        result["area_rectangles"] = [list(rect) for rect in rectangles]
    return result


def trigger_repaint(process: int, pid: int) -> str:
    window = find_xz_window(pid)
    if window is None:
        return "window_not_ready"
    _hwnd, this_ptr = window
    impl_module = find_module(pid, IMPL_PROFILE.name)
    dui_module = find_module(pid, "DuiLib64.dll")
    if impl_module is None or dui_module is None:
        return "module_not_ready"
    actual_impl_hash = sha256_file(impl_module.path)
    if actual_impl_hash != IMPL_4138_SHA256:
        raise BypassError(
            f"unsupported {IMPL_PROFILE.name} background layout: "
            f"sha256={actual_impl_hash}; "
            f"expected={IMPL_4138_SHA256}. No thread was created."
        )
    actual_dui_hash = sha256_file(dui_module.path)
    if actual_dui_hash != DUI_4138_SHA256:
        raise BypassError(
            f"unsupported DuiLib64.dll background function: sha256={actual_dui_hash}; "
            f"expected={DUI_4138_SHA256}. No thread was created."
        )
    entry_address = dui_module.base + DUI_SET_BK_IMAGE_RVA
    actual_entry = read_memory(process, entry_address, len(DUI_SET_BK_IMAGE_ENTRY))
    if actual_entry != DUI_SET_BK_IMAGE_ENTRY:
        raise BypassError(
            "DuiLib64 SetBkImage entry bytes changed; no thread was created: "
            f"{actual_entry.hex(' ').upper()}"
        )

    state_path = external_wallpaper_state_path()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        image_path = Path(state["output"])
    except (AttributeError, OSError, ValueError, KeyError, TypeError) as exc:
        raise BypassError(f"cannot read WDD span state {state_path}: {exc}") from exc
    if not image_path.is_file():
        raise BypassError(f"WDD span image is unavailable: {image_path}")

    control = struct.unpack(
        "<Q", read_memory(process, this_ptr + XZ_BACKGROUND_CONTROL_OFFSET, 8)
    )[0]
    if not control:
        return "background_control_not_ready"
    control_vtable = struct.unpack("<Q", read_memory(process, control, 8))[0]
    if not (
        impl_module.base
        <= control_vtable
        < impl_module.base + impl_module.size
    ):
        raise BypassError(
            f"unexpected XiaoZhi background control vtable: 0x{control_vtable:X}"
        )

    image_text = str(image_path)
    encoded_path = image_text.encode("utf-16-le") + b"\x00\x00"
    if len(encoded_path) > 0xD00:
        raise BypassError(f"WDD span image path is too long: {image_path}")
    allocation_value = kernel32.VirtualAllocEx(
        process,
        None,
        REMOTE_BACKGROUND_BLOCK_SIZE,
        MEM_COMMIT | MEM_RESERVE,
        PAGE_EXECUTE_READWRITE,
    )
    if not allocation_value:
        raise win_error("VirtualAllocEx(SetBkImage)")
    allocation = int(ctypes.cast(allocation_value, ctypes.c_void_p).value)
    empty_view_address = allocation + 0x180
    image_view_address = allocation + 0x190
    path_address = allocation + 0x200
    # Clear first so a virtual-desktop transition reloads even when WDD keeps
    # the same cache filename.  Both calls finish before the window can repaint.
    remote_code = (
        b"\x48\x83\xEC\x28"
        + b"\x48\xB9" + struct.pack("<Q", control)
        + b"\x48\xBA" + struct.pack("<Q", empty_view_address)
        + b"\x48\xB8" + struct.pack("<Q", entry_address)
        + b"\xFF\xD0"
        + b"\x48\xB9" + struct.pack("<Q", control)
        + b"\x48\xBA" + struct.pack("<Q", image_view_address)
        + b"\x48\xB8" + struct.pack("<Q", entry_address)
        + b"\xFF\xD0"
        + b"\x48\x83\xC4\x28\x31\xC0\xC3"
    )
    write_data_memory(process, allocation, remote_code)
    write_data_memory(process, empty_view_address, b"\x00" * 16)
    write_data_memory(
        process,
        image_view_address,
        struct.pack("<QQ", path_address, len(image_text)),
    )
    write_data_memory(process, path_address, encoded_path)

    thread_id = wintypes.DWORD()
    thread = kernel32.CreateRemoteThread(
        process,
        None,
        0,
        ctypes.c_void_p(allocation),
        None,
        0,
        ctypes.byref(thread_id),
    )
    if not thread:
        kernel32.VirtualFreeEx(process, allocation, 0, MEM_RELEASE)
        raise win_error("CreateRemoteThread(DuiLib64!SetBkImage)")
    completed = False
    try:
        wait_result = kernel32.WaitForSingleObject(thread, REPAINT_THREAD_TIMEOUT_MS)
        if wait_result == WAIT_TIMEOUT:
            raise BypassError(
                "DuiLib64!SetBkImage did not finish within "
                f"{REPAINT_THREAD_TIMEOUT_MS / 1000:g} seconds"
            )
        if wait_result != WAIT_OBJECT_0:
            raise win_error("WaitForSingleObject(SetBkImage thread)")
        completed = True
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeThread(thread, ctypes.byref(exit_code)):
            raise win_error("GetExitCodeThread(SetBkImage thread)")
    finally:
        close_handle(thread)
        if completed:
            kernel32.VirtualFreeEx(process, allocation, 0, MEM_RELEASE)
    return (
        f"xz_setbkimage_thread_{thread_id.value}_"
        f"exit_0x{exit_code.value:08X}"
    )


def repaint_live_pid(pid: int) -> str:
    process = open_process(pid)
    try:
        return trigger_repaint(process, pid)
    finally:
        close_handle(process)


def inspect_pid(pid: int) -> dict:
    modules = resolve_modules(pid)
    process = open_process(pid)
    module_results = []
    all_states: list[str] = []
    per_monitor: dict = {
        "state": "unknown" if PER_MONITOR_SYNC_ENABLED else "disabled_4.1.3.8"
    }
    try:
        for profile, module in modules:
            sites = []
            for site in profile.sites:
                address = module.base + site.rva
                current = read_memory(process, address, len(site.original))
                state = site_state(current, site)
                all_states.append(state)
                sites.append(
                    {
                        "name": site.name,
                        "rva": f"0x{site.rva:X}",
                        "address": f"0x{address:X}",
                        "state": state,
                        "bytes": current.hex(" ").upper(),
                    }
                )
            module_states = {site["state"] for site in sites}
            module_state = module_states.pop() if len(module_states) == 1 else "mixed"
            module_results.append(
                {
                    "name": profile.name,
                    "state": module_state,
                    "path": str(module.path),
                    "base": f"0x{module.base:X}",
                    "sha256": profile.sha256,
                    "sites": sites,
                }
            )
            if profile is IMPL_PROFILE:
                path_state, allocation = per_monitor_state(process, module)
                per_monitor = {"state": path_state}
                if allocation is not None:
                    per_monitor["allocation"] = f"0x{allocation:X}"
                all_states.append(path_state)
    finally:
        close_handle(process)

    states = set(all_states)
    overall = states.pop() if len(states) == 1 else "mixed"
    return {
        "pid": pid,
        "state": overall,
        "modules": module_results,
        "per_monitor_paths": per_monitor,
        "desktop_layer": desktop_layer_state(pid),
    }


def change_pid(pid: int, desired: str) -> dict:
    modules = resolve_modules(pid)
    impl_module = next(
        (module for profile, module in modules if profile is IMPL_PROFILE), None
    )
    process = open_process(pid)
    snapshots: list[tuple[ModuleProfile, ModuleInfo, PatchSite, bytes]] = []
    changed: list[tuple[int, bytes]] = []
    repaint = None
    wallpaper_sync = None
    try:
        if PER_MONITOR_SYNC_ENABLED and impl_module is not None:
            path_state, _allocation = per_monitor_state(process, impl_module)
            if path_state == "mismatch":
                raise BypassError(
                    f"pid={pid}: per-monitor path hook has unexpected bytes. No memory was changed."
                )
        for profile, module in modules:
            for site in profile.sites:
                address = module.base + site.rva
                current = read_memory(process, address, len(site.original))
                state = site_state(current, site)
                if state == "mismatch":
                    raise BypassError(
                        f"pid={pid} {profile.name} {site.name}: unexpected bytes at "
                        f"RVA 0x{site.rva:X}: {current.hex(' ').upper()}. "
                        "No memory was changed."
                    )
                snapshots.append((profile, module, site, current))

        if (
            desired == "original"
            and PER_MONITOR_SYNC_ENABLED
            and impl_module is not None
        ):
            restore_per_monitor_paths(process, impl_module)

        ordered = snapshots if desired == "patched" else list(reversed(snapshots))
        for _profile, module, site, current in ordered:
            state = site_state(current, site)
            payload = site.patch if desired == "patched" else site.original_patch_width
            if state == desired:
                continue
            address = module.base + site.rva
            rollback = current[: len(payload)]
            write_memory(process, address, payload)
            changed.append((address, rollback))

        for _profile, module, site, _current in snapshots:
            current = read_memory(process, module.base + site.rva, len(site.original))
            if site_state(current, site) != desired:
                raise BypassError(f"pid={pid} {site.name}: read-back verification failed")

        if (
            desired == "patched"
            and PER_MONITOR_SYNC_ENABLED
            and impl_module is not None
        ):
            wallpaper_sync = sync_per_monitor_paths(process, pid, impl_module)
            repaint = wallpaper_sync["repaint"]

    except Exception:
        for address, rollback in reversed(changed):
            try:
                write_memory(process, address, rollback)
            except Exception:
                pass
        raise
    finally:
        close_handle(process)

    result = inspect_pid(pid)
    if wallpaper_sync is not None:
        result["wallpaper_sync"] = wallpaper_sync
    if repaint is not None:
        result["repaint"] = repaint
    return result


def print_result(result: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    pid = result.get("pid", "?")
    print(f"PID {pid}: {result['state']}")
    for module in result.get("modules", []):
        print(f"  Module: {module['path']}")
        print(f"  Base:   {module['base']} ({module['state']})")
        for site in module.get("sites", []):
            print(
                f"    {site['name']}: {site['state']} "
                f"({site['rva']} -> {site['address']})"
            )
    per_monitor = result.get("per_monitor_paths")
    if per_monitor:
        detail = f" allocation={per_monitor['allocation']}" if per_monitor.get("allocation") else ""
        print(f"  Per-monitor paths: {per_monitor['state']}{detail}")
    desktop_layer = result.get("desktop_layer")
    if desktop_layer:
        detail = f" ({desktop_layer['hwnd']})" if desktop_layer.get("hwnd") else ""
        print(f"  Desktop layer: {desktop_layer['state']}{detail}")
    sync = result.get("wallpaper_sync")
    if sync:
        print(f"  Wallpaper sync: {sync['operation']}")
        print(f"    Left:  {sync['left']}")
        print(f"    Right: {sync['right']}")
    if result.get("repaint"):
        print(f"  Repaint: {result['repaint']}")


def process_once(
    command: str,
    requested_pid: int | None,
    as_json: bool,
    desktop_mode: str | None = None,
) -> int:
    desktop_wallpaper_restore: dict | None = None
    if command == "restore":
        desktop_wallpaper_restore = restore_virtual_desktop_wallpapers()
    if command == "patch":
        windynamic = find_windynamicdesktop_executable()
        if windynamic is not None:
            refresh_windynamicdesktop(windynamic)
            time.sleep(1.0)
    pids = find_processes(requested_pid)
    if not pids:
        if command == "restore":
            if as_json:
                print(json.dumps({"virtual_desktops": desktop_wallpaper_restore}, ensure_ascii=False))
            else:
                print(
                    "Virtual-desktop wallpaper records: "
                    f"{desktop_wallpaper_restore['state']} "
                    f"({desktop_wallpaper_restore['restored']})."
                )
            return 0
        message = {
            "state": "process_not_found",
            "target": TARGET_EXE,
            "requested_pid": requested_pid,
        }
        if as_json:
            print(json.dumps(message, ensure_ascii=False, indent=2))
        else:
            print(f"{TARGET_EXE} is not running.", file=sys.stderr)
        return 3

    exit_code = 0
    for pid in pids:
        try:
            if command == "status":
                result = inspect_pid(pid)
            elif command == "patch":
                result = change_pid(pid, "patched")
                result["desktop_layer"] = set_desktop_layer(pid, desktop_mode or "hybrid")
            else:
                result = change_pid(pid, "original")
                result["desktop_layer"] = set_desktop_layer(pid, "xz")
            print_result(result, as_json)
            if result["state"] in ("mismatch", "mixed", "module_not_loaded"):
                exit_code = 4
        except BypassError as exc:
            exit_code = 4
            if as_json:
                print(json.dumps({"pid": pid, "state": "error", "error": str(exc)}, ensure_ascii=False))
            else:
                print(f"PID {pid}: ERROR: {exc}", file=sys.stderr)
    if command == "restore":
        if as_json:
            print(json.dumps({"virtual_desktops": desktop_wallpaper_restore}, ensure_ascii=False))
        else:
            print(
                "Virtual-desktop wallpaper records: "
                f"{desktop_wallpaper_restore['state']} "
                f"({desktop_wallpaper_restore['restored']})."
            )
    return exit_code


def find_windynamicdesktop_executable() -> Path | None:
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == INVALID_HANDLE_VALUE:
        raise win_error("CreateToolhelp32Snapshot(processes)")
    candidates: list[tuple[int, str]] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            raise win_error("Process32FirstW")
        while True:
            if entry.szExeFile.lower().startswith(WINDYNAMIC_PREFIX):
                candidates.append((int(entry.th32ProcessID), entry.szExeFile))
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                if ctypes.get_last_error() != ERROR_NO_MORE_FILES:
                    raise win_error("Process32NextW")
                break
    finally:
        close_handle(snapshot)
    for pid, name in candidates:
        module = find_module(pid, name)
        if module is not None and module.path.is_file():
            return module.path
    return None


def windynamic_settings_path(executable: Path | None) -> Path | None:
    candidates: list[Path] = []
    if executable is not None:
        candidates.append(executable.parent / "settings.json")
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        candidates.append(Path(local_appdata) / "WinDynamicDesktop" / "settings.json")
    return next((path for path in candidates if path.is_file()), None)


def file_mtime(path: Path | None) -> int | None:
    try:
        return path.stat().st_mtime_ns if path is not None else None
    except OSError:
        return None


def file_marker(path: Path | None) -> tuple[int, int] | None:
    try:
        if path is None:
            return None
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def external_wallpaper_state_path() -> Path | None:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if not local_appdata:
        return None
    return Path(local_appdata) / "XZWallpaperBypass" / "WDD-Span" / "state.json"


def read_area_log_delta(
    path: Path,
    offset: int | None,
    carry: bytes,
) -> tuple[bool, int, bytes]:
    """Read only newly appended log bytes and flag real area-coordinate events."""
    stat = path.stat()
    size = stat.st_size
    if offset is None or size < offset:
        return True, size, b""
    if size == offset:
        return False, offset, carry
    with path.open("rb") as stream:
        stream.seek(offset)
        appended = stream.read(size - offset)
    probe = carry + appended
    changed = any(token in probe for token in XZ_AREA_EVENT_TOKENS)
    longest_token = max(len(token) for token in XZ_AREA_EVENT_TOKENS)
    return changed, size, probe[-(longest_token - 1) :]


def current_virtual_desktop_id() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, VIRTUAL_DESKTOP_ROOT) as key:
            value, _kind = winreg.QueryValueEx(key, "CurrentVirtualDesktop")
        if isinstance(value, bytes) and len(value) == 16:
            return str(uuid.UUID(bytes_le=value))
    except OSError:
        return None
    return None


def virtual_desktop_wallpaper_backup_path() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    return base / "XZWallpaperBypass" / VIRTUAL_DESKTOP_BACKUP_NAME


def query_virtual_desktop_wallpapers() -> list[dict]:
    desktops: list[dict] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, VIRTUAL_DESKTOPS_ROOT) as root:
            index = 0
            while True:
                try:
                    desktop_id = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                key_path = VIRTUAL_DESKTOPS_ROOT + "\\" + desktop_id
                present = True
                value = None
                kind = winreg.REG_SZ
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    try:
                        value, kind = winreg.QueryValueEx(key, "Wallpaper")
                    except FileNotFoundError:
                        present = False
                desktops.append(
                    {
                        "id": desktop_id,
                        "key_path": key_path,
                        "present": present,
                        "wallpaper": value if isinstance(value, str) else None,
                        "kind": kind,
                    }
                )
    except FileNotFoundError:
        return []
    return desktops


def write_virtual_desktop_backup(desktops: list[dict]) -> Path:
    backup_path = virtual_desktop_wallpaper_backup_path()
    data: dict = {"schema": 1, "created_unix": int(time.time()), "desktops": {}}
    if backup_path.is_file():
        try:
            loaded = json.loads(backup_path.read_text(encoding="utf-8"))
            if loaded.get("schema") == 1 and isinstance(loaded.get("desktops"), dict):
                data = loaded
        except (OSError, ValueError, TypeError):
            raise BypassError(f"cannot read wallpaper backup: {backup_path}")

    changed = False
    saved = data["desktops"]
    for desktop in desktops:
        if desktop["id"] in saved:
            continue
        saved[desktop["id"]] = {
            "present": desktop["present"],
            "wallpaper": desktop["wallpaper"],
            "kind": desktop["kind"],
        }
        changed = True
    if changed or not backup_path.is_file():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = backup_path.with_suffix(backup_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, backup_path)
    return backup_path


def clear_virtual_desktop_wallpapers() -> dict:
    """Make every virtual desktop inherit the current per-monitor wallpaper set."""
    desktops = query_virtual_desktop_wallpapers()
    backup_path = write_virtual_desktop_backup(desktops)
    changed = 0
    for desktop in desktops:
        if not desktop["present"]:
            continue
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            desktop["key_path"],
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            try:
                winreg.DeleteValue(key, "Wallpaper")
            except FileNotFoundError:
                continue
        changed += 1
    return {
        "state": "inherited",
        "desktops": len(desktops),
        "changed": changed,
        "backup": str(backup_path),
    }


def restore_virtual_desktop_wallpapers() -> dict:
    backup_path = virtual_desktop_wallpaper_backup_path()
    if not backup_path.is_file():
        return {"state": "not_backed_up", "restored": 0, "backup": str(backup_path)}
    try:
        data = json.loads(backup_path.read_text(encoding="utf-8"))
        saved = data["desktops"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise BypassError(f"cannot read wallpaper backup {backup_path}: {exc}") from exc
    restored = 0
    for desktop_id, original in saved.items():
        key_path = VIRTUAL_DESKTOPS_ROOT + "\\" + desktop_id
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
            ) as key:
                if original.get("present"):
                    winreg.SetValueEx(
                        key,
                        "Wallpaper",
                        0,
                        int(original.get("kind", winreg.REG_SZ)),
                        original.get("wallpaper") or "",
                    )
                else:
                    try:
                        winreg.DeleteValue(key, "Wallpaper")
                    except FileNotFoundError:
                        pass
                restored += 1
        except FileNotFoundError:
            continue
    return {"state": "restored", "restored": restored, "backup": str(backup_path)}


def refresh_windynamicdesktop(executable: Path | None) -> str:
    if executable is None or not executable.is_file():
        return "not_running"
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    completed = subprocess.run(
        [str(executable), "/refresh"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=20,
        creationflags=creationflags,
        check=False,
    )
    if completed.returncode != 0:
        raise BypassError(f"WinDynamicDesktop /refresh exited {completed.returncode}")
    return "refreshed"


def wallpaper_signature(monitors: list[dict]) -> tuple:
    return tuple(
        (tuple(monitor["rect"]), monitor["wallpaper"])
        for monitor in sorted(monitors, key=lambda item: item["rect"][0])
    )


def sync_live_pid(pid: int, monitors: list[dict]) -> dict:
    module = find_module(pid, IMPL_PROFILE.name)
    if module is None:
        raise BypassError(f"pid={pid}: {IMPL_PROFILE.name} is not loaded yet")
    validate_target(module, IMPL_PROFILE)
    process = open_process(pid)
    try:
        return sync_per_monitor_paths(process, pid, module, monitors)
    finally:
        close_handle(process)


def watch(
    interval: float,
    requested_pid: int | None,
    as_json: bool,
    desktop_mode: str,
    wallpaper_refresh: str,
    startup_delay: float,
) -> int:
    manage_wallpaper = wallpaper_refresh == "windynamic"
    external_state = external_wallpaper_state_path() if not manage_wallpaper else None
    windynamic = find_windynamicdesktop_executable() if manage_wallpaper else None
    settings = windynamic_settings_path(windynamic) if manage_wallpaper else None
    if as_json:
        print(
            json.dumps(
                {
                    "state": "watching",
                    "target": TARGET_EXE,
                    "interval": interval,
                    "desktop_mode": desktop_mode,
                    "wallpaper_refresh": wallpaper_refresh,
                    "startup_delay": startup_delay,
                    "windynamicdesktop": str(windynamic) if windynamic else None,
                }
            )
        )
    else:
        print(
            f"Watching {TARGET_EXE}; Ctrl+C to stop. "
            f"Desktop mode: {desktop_mode}. Installed files are never modified."
        )
        if not manage_wallpaper:
            print(
                "Wallpaper refresh: external WDD script; watcher refreshes the "
                "desktop window after the WDD state file changes."
            )
        elif windynamic:
            print(f"WinDynamicDesktop: {windynamic}")
        else:
            print("WinDynamicDesktop is not running; current Windows paths will still be mirrored.")
        print(
            f"Startup safety: wait {startup_delay:.1f}s after XZDesktopWnd is ready "
            "before applying the memory patch."
        )
    last_report: dict[int, str] = {}
    synced_signatures: dict[int, tuple] = {}
    status_cache: dict[int, dict] = {}
    area_sources: dict[int, Path] = {}
    area_markers: dict[int, tuple[int, int] | None] = {}
    area_offsets: dict[int, int] = {}
    area_carries: dict[int, bytes] = {}
    next_verification: dict[int, float] = {}
    external_refreshed_generations: dict[int, int] = {}
    desktop_ready_since: dict[int, float] = {}
    drag_active_until = 0.0
    live_pids_cache: set[int] = set()
    next_process_scan = 0.0
    next_windynamic_scan = 0.0
    settings_marker = file_mtime(settings)
    desktop_marker = current_virtual_desktop_id()
    watch_started = time.monotonic()
    external_state_marker: tuple[int, int] | None = None
    # Generation one guarantees one verified XiaoZhi-side redraw after a new
    # process is patched, even when WDD's state file predates this watcher.
    external_refresh_generation = 1 if not manage_wallpaper else 0
    external_refresh_due = watch_started
    external_refresh_reason = "startup"
    pending_refresh_reasons: set[str] = {"startup"} if manage_wallpaper else set()
    refresh_due = watch_started + 1.0
    last_refresh_request = watch_started - REFRESH_COOLDOWN_SECONDS
    refresh_error: str | None = None
    next_inheritance_check = 0.0
    inheritance_error: str | None = None
    try:
        while True:
            loop_clock = time.monotonic()
            refresh_happened = False
            if external_state is not None:
                new_external_marker = file_marker(external_state)
                if new_external_marker != external_state_marker:
                    external_state_marker = new_external_marker
                    if new_external_marker is not None:
                        external_refresh_generation += 1
                        external_refresh_reason = "external_wdd_state"
                        external_refresh_due = (
                            loop_clock + EXTERNAL_LAYER_REFRESH_DEBOUNCE_SECONDS
                        )
            if (
                manage_wallpaper
                and
                (windynamic is None or not windynamic.is_file())
                and loop_clock >= next_windynamic_scan
            ):
                windynamic = find_windynamicdesktop_executable()
                settings = windynamic_settings_path(windynamic)
                settings_marker = file_mtime(settings)
                next_windynamic_scan = loop_clock + 2.0
                if windynamic is not None:
                    pending_refresh_reasons.add("windynamicdesktop_started")
                    refresh_due = max(
                        time.monotonic() + 1.0,
                        last_refresh_request + REFRESH_COOLDOWN_SECONDS,
                    )

            new_settings_marker = file_mtime(settings) if manage_wallpaper else settings_marker
            new_desktop_marker = current_virtual_desktop_id()
            event_now = time.monotonic()
            if manage_wallpaper and new_settings_marker != settings_marker:
                pending_refresh_reasons.add("theme_settings_changed")
                settings_marker = new_settings_marker
                refresh_due = max(
                    event_now + REFRESH_DEBOUNCE_SECONDS,
                    last_refresh_request + REFRESH_COOLDOWN_SECONDS,
                )
            if manage_wallpaper and new_desktop_marker != desktop_marker:
                pending_refresh_reasons.add("virtual_desktop_changed")
                desktop_marker = new_desktop_marker
                refresh_due = max(
                    event_now + REFRESH_DEBOUNCE_SECONDS,
                    last_refresh_request + REFRESH_COOLDOWN_SECONDS,
                )
            elif (
                not manage_wallpaper
                and new_desktop_marker is not None
                and new_desktop_marker != desktop_marker
            ):
                desktop_marker = new_desktop_marker
                external_refresh_generation += 1
                external_refresh_reason = "virtual_desktop_changed"
                external_refresh_due = (
                    event_now + EXTERNAL_LAYER_REFRESH_DEBOUNCE_SECONDS
                )

            inheritance_state: dict | None = None
            inheritance_now = time.monotonic()
            if manage_wallpaper and inheritance_now >= next_inheritance_check:
                try:
                    inheritance_state = clear_virtual_desktop_wallpapers()
                    if inheritance_state["changed"] and not as_json:
                        print(
                            "Virtual desktops now inherit the system per-monitor "
                            f"wallpapers ({inheritance_state['changed']} overrides removed)."
                        )
                    inheritance_error = None
                except (BypassError, OSError) as exc:
                    message = str(exc)
                    if message != inheritance_error and not as_json:
                        print(f"Virtual-desktop inheritance waiting/error: {message}", file=sys.stderr)
                    inheritance_error = message
                next_inheritance_check = (
                    inheritance_now + VIRTUAL_DESKTOP_ENFORCE_SECONDS
                )
            if (
                manage_wallpaper
                and pending_refresh_reasons
                and time.monotonic() >= refresh_due
            ):
                refresh_reasons = sorted(pending_refresh_reasons)
                try:
                    refresh_state = refresh_windynamicdesktop(windynamic)
                    if refresh_state == "not_running":
                        raise BypassError("WinDynamicDesktop is not running")
                    last_refresh_request = time.monotonic()
                    pending_refresh_reasons.clear()
                    refresh_error = None
                    refresh_happened = True
                    event = {
                        "state": "wallpaper_refresh",
                        "reason": refresh_reasons,
                        "windynamicdesktop": refresh_state,
                        "virtual_desktop": desktop_marker,
                        "virtual_desktop_inheritance": inheritance_state,
                    }
                    if as_json:
                        print(json.dumps(event, ensure_ascii=False))
                    else:
                        print(
                            f"Wallpaper refresh ({', '.join(refresh_reasons)}): "
                            f"{refresh_state}"
                        )
                except (BypassError, OSError, subprocess.SubprocessError) as exc:
                    last_refresh_request = time.monotonic()
                    refresh_due = last_refresh_request + REFRESH_RETRY_SECONDS
                    message = str(exc)
                    if message != refresh_error:
                        if as_json:
                            print(
                                json.dumps(
                                    {
                                        "state": "wallpaper_refresh_error",
                                        "reason": refresh_reasons,
                                        "error": message,
                                        "retry_seconds": REFRESH_RETRY_SECONDS,
                                    },
                                    ensure_ascii=False,
                                )
                            )
                        else:
                            print(
                                f"Wallpaper refresh waiting/error: {message}; "
                                f"retrying in {REFRESH_RETRY_SECONDS:.0f}s",
                                file=sys.stderr,
                            )
                    refresh_error = message

            monitors: list[dict] | None = None
            signature: tuple | None = None
            monitor_error: Exception | None = None
            if PER_MONITOR_SYNC_ENABLED:
                try:
                    monitors = query_monitor_wallpapers()
                    validate_wallpapers(monitors)
                    signature = wallpaper_signature(monitors)
                except (BypassError, OSError) as exc:
                    monitor_error = exc

            loop_now = time.monotonic()
            if loop_now >= next_process_scan:
                live_pids_cache = set(find_processes(requested_pid))
                next_process_scan = loop_now + (1.0 if live_pids_cache else 0.25)
            live_pids = live_pids_cache
            for stale_pid in set(last_report) - live_pids:
                if not as_json:
                    print(f"PID {stale_pid}: exited")
                last_report.pop(stale_pid, None)
                synced_signatures.pop(stale_pid, None)
                status_cache.pop(stale_pid, None)
                area_sources.pop(stale_pid, None)
                area_markers.pop(stale_pid, None)
                area_offsets.pop(stale_pid, None)
                area_carries.pop(stale_pid, None)
                next_verification.pop(stale_pid, None)
                external_refreshed_generations.pop(stale_pid, None)
                desktop_ready_since.pop(stale_pid, None)
            for pid in sorted(live_pids):
                try:
                    now = time.monotonic()
                    full_check = (
                        PER_MONITOR_SYNC_ENABLED
                        or pid not in status_cache
                        or now >= next_verification.get(pid, 0.0)
                    )
                    if full_check:
                        status = inspect_pid(pid)
                        if status["state"] != "patched":
                            layer_state = status.get("desktop_layer", {}).get("state")
                            if layer_state == "window_not_ready":
                                desktop_ready_since.pop(pid, None)
                                wait_reason = "XZDesktopWnd is not ready"
                            else:
                                ready_since = desktop_ready_since.setdefault(pid, now)
                                remaining = startup_delay - (now - ready_since)
                                wait_reason = (
                                    f"startup settle delay ({remaining:.1f}s remaining)"
                                    if remaining > 0
                                    else None
                                )
                            if wait_reason is not None:
                                marker = f"startup_wait:{layer_state}"
                                if last_report.get(pid) != marker:
                                    if as_json:
                                        print(
                                            json.dumps(
                                                {
                                                    "pid": pid,
                                                    "state": "waiting",
                                                    "reason": wait_reason,
                                                }
                                            )
                                        )
                                    else:
                                        print(f"PID {pid}: waiting: {wait_reason}")
                                    last_report[pid] = marker
                                time.sleep(interval)
                                continue
                            status = change_pid(pid, "patched")
                        next_verification[pid] = now + 30.0
                    else:
                        status = dict(status_cache[pid])
                    repaint_state = status.get("repaint", "")
                    if (
                        PER_MONITOR_SYNC_ENABLED
                        and status["state"] == "patched"
                        and signature is not None
                    ):
                        if synced_signatures.get(pid) != signature:
                            sync = sync_live_pid(pid, monitors or [])
                            status = inspect_pid(pid)
                            status["wallpaper_sync"] = sync
                            status["repaint"] = sync["repaint"]
                            repaint_state = sync["repaint"]
                        if repaint_state != "window_not_ready":
                            synced_signatures[pid] = signature
                    elif monitor_error is not None:
                        raise monitor_error

                    external_repaint_refresh = (
                        status["state"] == "patched"
                        and external_refresh_generation > 0
                        and external_refreshed_generations.get(pid, 0)
                        != external_refresh_generation
                        and time.monotonic() >= external_refresh_due
                    )
                    if external_repaint_refresh:
                        repaint_state = repaint_live_pid(pid)
                        status["repaint"] = repaint_state
                        if repaint_state not in ("window_not_ready", "module_not_ready"):
                            external_refreshed_generations[pid] = (
                                external_refresh_generation
                            )
                            status["desktop_layer_refresh"] = external_refresh_reason

                    source = area_sources.get(pid)
                    layer_refresh = full_check or refresh_happened
                    if desktop_mode == "hybrid":
                        if source is None:
                            layer_refresh = True
                        else:
                            area_changed, new_offset, new_carry = read_area_log_delta(
                                source,
                                area_offsets.get(pid),
                                area_carries.get(pid, b""),
                            )
                            area_offsets[pid] = new_offset
                            area_carries[pid] = new_carry
                            if area_changed:
                                layer_refresh = True
                                drag_active_until = now + 0.5
                    if layer_refresh:
                        layer = set_desktop_layer(pid, desktop_mode)
                        source_value = layer.get("area_source")
                        if source_value:
                            source = Path(source_value)
                            area_sources[pid] = source
                            marker = file_marker(source)
                            area_markers[pid] = marker
                            if marker is not None:
                                area_offsets[pid] = marker[1]
                                area_carries[pid] = b""
                        status["desktop_layer"] = layer
                    else:
                        layer = status.get("desktop_layer", {"state": "unknown"})
                    status_cache[pid] = status
                    marker = (
                        f"{status['state']}:{status.get('repaint', '')}:"
                        f"{synced_signatures.get(pid, '')}:{layer['state']}"
                    )
                    if last_report.get(pid) != marker:
                        print_result(status, as_json)
                        last_report[pid] = marker
                except (BypassError, OSError) as exc:
                    next_verification[pid] = 0.0
                    area_markers.pop(pid, None)
                    area_offsets.pop(pid, None)
                    area_carries.pop(pid, None)
                    marker = f"error:{exc}"
                    if last_report.get(pid) != marker:
                        if as_json:
                            print(json.dumps({"pid": pid, "state": "error", "error": str(exc)}))
                        else:
                            print(f"PID {pid}: waiting/error: {exc}", file=sys.stderr)
                        last_report[pid] = marker
            sleep_interval = 0.02 if time.monotonic() < drag_active_until else interval
            time.sleep(sleep_interval)
    except KeyboardInterrupt:
        if not as_json:
            print("Watch stopped. Existing in-memory patches remain until restore or process exit.")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="XZWallpaperBypass",
        description=(
            "Runtime-only wallpaper compatibility fix for "
            "XiaoZhi Desktop 4.1.3.8 x64, including WinDynamicDesktop "
            "integration for virtual desktops. "
            "It does not modify any installed file."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    parser.add_argument("--pid", type=int, help="operate on one XZDesktop64 process only")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="verify target version and show patch state")
    patch_parser = subparsers.add_parser(
        "patch",
        help="fix wallpaper handling and select the desktop input layer",
    )
    patch_parser.add_argument(
        "--desktop-mode",
        choices=("hybrid", "system", "xz"),
        default="xz",
        help="xz keeps native XiaoZhi desktop input; hybrid is an optional legacy mode",
    )
    subparsers.add_parser("restore", help="restore all original bytes in memory")
    watch_parser = subparsers.add_parser(
        "watch",
        help=(
            "patch XiaoZhi and mirror WinDynamicDesktop after Apply, scheduled "
            "changes, and virtual-desktop switches"
        ),
    )
    watch_parser.add_argument(
        "--interval",
        type=float,
        default=0.05,
        help="poll interval in seconds (default: 0.05)",
    )
    watch_parser.add_argument(
        "--desktop-mode",
        choices=("hybrid", "system", "xz"),
        default="xz",
        help="continuously enforce native XiaoZhi, hybrid, or system desktop handling",
    )
    watch_parser.add_argument(
        "--wallpaper-refresh",
        choices=("windynamic", "external"),
        default="windynamic",
        help=(
            "windynamic calls /refresh on virtual-desktop changes; external leaves "
            "wallpaper state to an installed WDD script"
        ),
    )
    watch_parser.add_argument(
        "--startup-delay",
        type=float,
        default=STARTUP_SETTLE_SECONDS,
        help=(
            "seconds to wait after XZDesktopWnd is ready before patching "
            f"(default: {STARTUP_SETTLE_SECONDS:g})"
        ),
    )
    return parser


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
        sys.stderr.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
    if sys.platform != "win32":
        print("This tool only runs on Windows.", file=sys.stderr)
        return 2
    if struct.calcsize("P") != 8:
        print("A 64-bit build is required.", file=sys.stderr)
        return 2
    args = build_parser().parse_args()
    try:
        if args.command == "watch":
            if args.interval < 0.05:
                raise BypassError("--interval must be at least 0.05 seconds")
            if args.startup_delay < 0:
                raise BypassError("--startup-delay cannot be negative")
            return watch(
                args.interval,
                args.pid,
                args.json,
                args.desktop_mode,
                args.wallpaper_refresh,
                args.startup_delay,
            )
        return process_once(
            args.command,
            args.pid,
            args.json,
            getattr(args, "desktop_mode", None),
        )
    except (BypassError, OSError) as exc:
        if args.json:
            print(json.dumps({"state": "error", "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
