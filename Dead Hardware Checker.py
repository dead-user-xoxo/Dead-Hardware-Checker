import sys
import platform
import subprocess
import threading
import tkinter as tk
from tkinter import scrolledtext

if sys.platform != "win32":
    print("This script only works on Windows (it reads the Windows registry "
          "and uses PowerShell/WMI). Run it on a Windows machine.")
    sys.exit(1)

import winreg  # noqa: E402  (Windows-only import)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

def run_powershell(command: str) -> str:
    """Run a PowerShell command and return its trimmed stdout."""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True, text=True, timeout=20
        )
        out = result.stdout.strip()
        if not out and result.stderr.strip():
            return f"(error: {result.stderr.strip()[:120]})"
        return out
    except Exception as e:
        return f"(error: {e})"


def read_registry_value(hive, path, name):
    try:
        key = winreg.OpenKey(hive, path)
        value = winreg.QueryValueEx(key, name)[0]
        winreg.CloseKey(key)
        return value
    except Exception as e:
        return f"(error: {e})"


def get_windows_version() -> str:
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        product_name = winreg.QueryValueEx(key, "ProductName")[0]
        try:
            display_version = winreg.QueryValueEx(key, "DisplayVersion")[0]
        except FileNotFoundError:
            display_version = winreg.QueryValueEx(key, "ReleaseId")[0]
        build = winreg.QueryValueEx(key, "CurrentBuildNumber")[0]
        try:
            ubr = winreg.QueryValueEx(key, "UBR")[0]
            build_full = f"{build}.{ubr}"
        except FileNotFoundError:
            build_full = build
        winreg.CloseKey(key)
        return f"{product_name} {display_version} (Build {build_full})"
    except Exception as e:
        return f"Unable to retrieve Windows version ({e})"


def decode_digital_product_id(digital_product_id) -> str:
    """Classic offline decode of the DigitalProductId registry value into
    a 25-character product key. Works for keys stored locally on the
    installed system (pre-OA3x era format)."""
    key_offset = 52
    chars = "BCDFGHJKMPQRTVWXY2346789"
    data = bytearray(digital_product_id)
    is_win8 = (data[66] // 6) & 1
    data[66] = (data[66] & 0xF7) | ((is_win8 & 2) * 4)

    key_output = ""
    last = 0
    for i in range(24, -1, -1):
        current = 0
        for j in range(14, -1, -1):
            current = current * 256
            current = data[j + key_offset] + current
            data[j + key_offset] = current // 24
            current = current % 24
            last = current
        key_output = chars[current] + key_output

    key_part1 = key_output[1:last + 1]
    key_part2 = key_output[last + 1:]
    if last == 0:
        key_output = "N" + key_part2
    else:
        key_output = key_part1 + "N" + key_part2

    result = "-".join(key_output[i:i + 5] for i in range(0, len(key_output), 5))
    return result


def get_windows_product_key() -> str:
    # Try the modern OEM/digital-entitlement key first (Windows 8+)
    oa3x = run_powershell(
        "(Get-CimInstance -Query 'select OA3xOriginalProductKey from "
        "SoftwareLicensingService').OA3xOriginalProductKey"
    )
    if oa3x and "error" not in oa3x.lower() and len(oa3x.strip()) >= 25:
        return oa3x.strip()

    # Fallback: decode DigitalProductId from the registry
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                              r"SOFTWARE\Microsoft\Windows NT\CurrentVersion")
        digital_product_id = winreg.QueryValueEx(key, "DigitalProductId")[0]
        winreg.CloseKey(key)
        return decode_digital_product_id(digital_product_id)
    except Exception as e:
        return f"Unable to retrieve product key ({e})"


def get_os_edition() -> str:
    """Returns the OS edition/caption currently installed. Note: this is
    the installed edition, not something derived from the product key
    itself (Windows doesn't expose a reliable local mapping from key ->
    edition without contacting activation servers)."""
    edition = run_powershell(
        "(Get-CimInstance -ClassName Win32_OperatingSystem).Caption"
    )
    return edition if edition else "Unknown"


def get_cpu() -> str:
    name = run_powershell(
        "(Get-CimInstance -ClassName Win32_Processor).Name"
    )
    return name if name else platform.processor()


def get_gpu() -> str:
    names = run_powershell(
        "(Get-CimInstance -ClassName Win32_VideoController).Name"
    )
    return names.replace("\r\n", ", ") if names else "Unknown"


def get_memory() -> str:
    total_bytes = run_powershell(
        "(Get-CimInstance -ClassName Win32_ComputerSystem).TotalPhysicalMemory"
    )
    try:
        gb = int(total_bytes) / (1024 ** 3)
        return f"{gb:.1f} GB"
    except Exception:
        return "Unknown"


def get_disks() -> str:
    """Returns total capacity plus a per-disk model/size breakdown."""
    output = run_powershell(
        "Get-CimInstance -ClassName Win32_DiskDrive | "
        "ForEach-Object { \"$($_.Model)|$($_.Size)\" }"
    )
    lines = [l for l in output.splitlines() if "|" in l]
    if not lines:
        return "Unknown"
    entries = []
    total = 0
    for l in lines:
        model, _, size_str = l.partition("|")
        try:
            size = int(size_str.strip())
            total += size
            entries.append(f"{model.strip()} ({size / (1024**3):.0f} GB)")
        except ValueError:
            entries.append(model.strip())
    total_gb = total / (1024 ** 3)
    return f"{total_gb:.0f} GB total across {len(entries)} disk(s) [{', '.join(entries)}]"


# ----------------------------------------------------------------------
# Hardware / Windows identifiers
# ----------------------------------------------------------------------

def get_machine_guid() -> str:
    return str(read_registry_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Cryptography",
        "MachineGuid"
    ))


def get_windows_product_id() -> str:
    """The 'Product ID' shown under Settings > About (AAAAA-BBBBB-CCCCC...),
    distinct from the 25-character product key."""
    return str(read_registry_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows NT\CurrentVersion",
        "ProductId"
    ))


def get_install_date() -> str:
    date_str = run_powershell(
        "(Get-CimInstance -ClassName Win32_OperatingSystem).InstallDate"
    )
    return date_str if date_str else "Unknown"


def get_system_uuid() -> str:
    uuid = run_powershell(
        "(Get-CimInstance -ClassName Win32_ComputerSystemProduct).UUID"
    )
    return uuid if uuid else "Unknown"


def get_bios_serial() -> str:
    serial = run_powershell(
        "(Get-CimInstance -ClassName Win32_BIOS).SerialNumber"
    )
    return serial if serial else "Unknown"


def get_motherboard_info() -> str:
    info = run_powershell(
        "$b = Get-CimInstance -ClassName Win32_BaseBoard; "
        "\"$($b.Manufacturer) $($b.Product) (S/N: $($b.SerialNumber))\""
    )
    return info if info else "Unknown"


def get_cpu_id() -> str:
    pid = run_powershell(
        "(Get-CimInstance -ClassName Win32_Processor).ProcessorId"
    )
    return pid if pid else "Unknown"


def get_disk_serials() -> str:
    output = run_powershell(
        "Get-CimInstance -ClassName Win32_DiskDrive | "
        "ForEach-Object { \"$($_.Model)|$($_.SerialNumber)\" }"
    )
    lines = [l for l in output.splitlines() if "|" in l]
    if not lines:
        return "Unknown"
    entries = []
    for l in lines:
        model, _, serial = l.partition("|")
        serial = serial.strip() or "N/A"
        entries.append(f"{model.strip()}: {serial}")
    return "; ".join(entries)


def get_mac_addresses() -> str:
    output = run_powershell(
        "Get-CimInstance -ClassName Win32_NetworkAdapter | "
        "Where-Object { $_.PhysicalAdapter -eq $true -and $_.MACAddress } | "
        "ForEach-Object { \"$($_.Name): $($_.MACAddress)\" }"
    )
    return output.replace("\r\n", " | ") if output else "Unknown"


def get_hardware_identifiers() -> list:
    """Returns a list of (label, value) tuples for the identifiers section."""
    return [
        ("Windows Machine GUID", get_machine_guid()),
        ("Windows Product ID", get_windows_product_id()),
        ("System UUID", get_system_uuid()),
        ("BIOS Serial Number", get_bios_serial()),
        ("Motherboard", get_motherboard_info()),
        ("CPU Processor ID", get_cpu_id()),
        ("Disk Serial Numbers", get_disk_serials()),
        ("Network MAC Addresses", get_mac_addresses()),
        ("Windows Install Date", get_install_date()),
    ]


# ----------------------------------------------------------------------
# GUI
# ----------------------------------------------------------------------

class DeadHardwareChecker(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Dead Hardware Checker")
        self.geometry("700x600")
        self.configure(bg="#1e1e1e")

        title_label = tk.Label(
            self, text="Dead Hardware Checker",
            font=("Segoe UI", 20, "bold"), fg="#e63946", bg="#1e1e1e"
        )
        title_label.pack(pady=(15, 5))

        spacer = tk.Label(self, text="", bg="#1e1e1e")
        spacer.pack(pady=2)

        checking_label = tk.Label(
            self, text="Checking your hardware...",
            font=("Segoe UI", 12), fg="#cccccc", bg="#1e1e1e"
        )
        checking_label.pack(pady=(0, 10))

        self.report_box = scrolledtext.ScrolledText(
            self, width=84, height=26, font=("Consolas", 10),
            bg="#111111", fg="#eeeeee", insertbackground="white"
        )
        self.report_box.pack(padx=10, pady=5)
        self.report_box.insert(tk.END, "Hardware report:\n\n")
        self.report_box.configure(state="disabled")

        self.status_var = tk.StringVar(value="Starting...")
        status_bar = tk.Label(
            self, textvariable=self.status_var, anchor="w",
            font=("Segoe UI", 9), fg="#00c853", bg="#000000"
        )
        status_bar.pack(side="bottom", fill="x")

        # Run the slow PowerShell/WMI work off the main thread so the UI
        # doesn't freeze, but only ever touch Tkinter widgets via
        # self.after(...), since Tkinter itself is not thread-safe.
        threading.Thread(target=self.run_checks, daemon=True).start()

    def set_status(self, text):
        self.after(0, lambda: self.status_var.set(text))

    def append_report(self, line):
        def _do_append():
            self.report_box.configure(state="normal")
            self.report_box.insert(tk.END, line + "\n")
            self.report_box.configure(state="disabled")
            self.report_box.see(tk.END)
        self.after(0, _do_append)

    def run_checks(self):
        self.set_status("Checking Windows version...")
        winver = get_windows_version()
        self.append_report(f"Windows version: {winver}")

        self.set_status("Checking Windows product key...")
        key = get_windows_product_key()
        self.append_report(f"Windows key: {key}")

        self.set_status("Checking OS edition...")
        edition = get_os_edition()
        self.append_report(f"OS edition: {edition}")

        self.set_status("Checking CPU...")
        self.append_report(f"CPU: {get_cpu()}")

        self.set_status("Checking GPU...")
        self.append_report(f"GPU: {get_gpu()}")

        self.set_status("Checking memory...")
        self.append_report(f"Memory: {get_memory()}")

        self.set_status("Checking disks...")
        self.append_report(f"Disk: {get_disks()}")

        self.set_status("Collecting hardware identifiers...")
        self.append_report("")
        self.append_report("---- Hardware & Windows Identifiers ----")
        for label, value in get_hardware_identifiers():
            self.append_report(f"{label}: {value}")

        self.set_status("Done.")


if __name__ == "__main__":
    app = DeadHardwareChecker()
    app.mainloop()
