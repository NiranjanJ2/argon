#!/bin/zsh

set -euo pipefail

SSD_MOUNT="${ARGON_SSD_MOUNT:-/Volumes/Storage}"
SSD_ALIAS="${ARGON_SSD_ALIAS:-$HOME/Library/Developer/ArgonSSD}"
CORE_MOUNT="${ARGON_CORE_SIMULATOR_MOUNT:-$HOME/Library/Developer/CoreSimulator/Devices}"
XCTEST_MOUNT="${ARGON_XCTEST_MOUNT:-$HOME/Library/Developer/XCTestDevices}"
CORE_BUNDLE="$SSD_ALIAS/Developer/ArgonSimulator.sparsebundle"
XCTEST_BUNDLE="$SSD_ALIAS/Developer/ArgonXCTestDevices.sparsebundle"
ATTACHED_CORE=0

if [[ ! -d "$SSD_MOUNT" ]]; then
  print -u2 "Argon simulator storage is offline: $SSD_MOUNT is not mounted"
  exit 1
fi

if [[ -L "$SSD_ALIAS" ]]; then
  [[ "$(readlink "$SSD_ALIAS")" == "$SSD_MOUNT" ]] || {
    print -u2 "Argon SSD alias points somewhere unexpected: $SSD_ALIAS"
    exit 1
  }
elif [[ -e "$SSD_ALIAS" ]]; then
  print -u2 "Argon SSD alias is occupied by a non-symlink: $SSD_ALIAS"
  exit 1
else
  ln -s "$SSD_MOUNT" "$SSD_ALIAS"
fi

attach_image() {
  local expected_name="$1"
  local mount_point="$2"
  local bundle="$3"
  local mounted_name

  mounted_name="$(diskutil info -plist "$mount_point" 2>/dev/null | plutil -extract VolumeName raw -o - - 2>/dev/null || true)"
  if [[ "$mounted_name" == "$expected_name" ]]; then
    return
  fi

  [[ -d "$bundle" ]] || {
    print -u2 "Missing simulator storage image: $bundle"
    exit 1
  }

  mkdir -p "$mount_point"
  if [[ -n "$(find "$mount_point" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    print -u2 "Refusing to cover non-empty simulator directory: $mount_point"
    exit 1
  fi

  hdiutil attach -quiet -nobrowse -owners on -mountpoint "$mount_point" "$bundle"
  if [[ "$expected_name" == "ArgonSimulator" ]]; then
    ATTACHED_CORE=1
  fi
}

attach_image "ArgonSimulator" "$CORE_MOUNT" "$CORE_BUNDLE"
attach_image "ArgonXCTestDevices" "$XCTEST_MOUNT" "$XCTEST_BUNDLE"

if (( ATTACHED_CORE )); then
  SERVICE_PID="$(pgrep -f 'com.apple.CoreSimulator.CoreSimulatorService.xpc/Contents/MacOS/com.apple.CoreSimulator.CoreSimulatorService' | head -n 1 || true)"
  if [[ -n "$SERVICE_PID" ]]; then
    kill "$SERVICE_PID" 2>/dev/null || true
    sleep 2
  fi
  xcrun simctl list devices >/dev/null
fi

print "Argon simulator storage is ready on $SSD_MOUNT"
