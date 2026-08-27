import DeviceActivity
import Foundation
import OSLog
import SwiftData

private let log = Logger(subsystem: "com.niranjanj.argon", category: "ArgonRoutine")

/// Tonight's shape, as the server describes it in `/v1/status`.
///
/// The server no longer decides *when*. It reports the time he chose in the
/// daily form — or the default he gets by not filling it in — and the phone
/// keeps it. See `ArgonRoutineActivity` for why the clock moved here.
struct ArgonRoutine: Codable, Equatable {
  /// "HH:MM". His answer if he gave one, otherwise `defaultStart`.
  let startAt: String
  /// Did he actually fill in the form today?
  let chosen: Bool
  let defaultStart: String
  let plannedToday: Bool
  /// Weekday numbers, Python-style: Monday 0 … Sunday 6.
  let schoolNights: [Int]
  let windowMinutes: Int
  let warningMinutes: Int

  enum CodingKeys: String, CodingKey {
    case startAt = "start_at"
    case chosen
    case defaultStart = "default_start"
    case plannedToday = "planned_today"
    case schoolNights = "school_nights"
    case windowMinutes = "window_minutes"
    case warningMinutes = "warning_minutes"
  }

  var startComponents: (hour: Int, minute: Int)? {
    let parts = startAt.split(separator: ":")
    guard parts.count == 2, let hour = Int(parts[0]), let minute = Int(parts[1]),
      (0..<24).contains(hour), (0..<60).contains(minute)
    else {
      return nil
    }
    return (hour, minute)
  }
}

/// Keeps the device's schedule matching what the server last reported.
@MainActor
enum ArgonRoutineScheduler {
  private static let defaults = UserDefaults.standard
  private static let appliedKey = "argon.routine.applied"

  /// Rewrite the schedule only when it actually changed.
  ///
  /// `startMonitoring` on an unchanged interval is not free — it tears the
  /// activity down and rebuilds it, and a rebuild that lands mid-interval
  /// drops the shield that is currently up. `refreshStatus` runs every twenty
  /// seconds while the app is open, so this has to be a no-op almost always.
  static func apply(_ routine: ArgonRoutine, profileId: UUID) {
    ArgonRoutineSettings.save(schoolNights: routine.schoolNights)

    let fingerprint = "\(profileId.uuidString)|\(routine.startAt)|\(routine.windowMinutes)"
    guard defaults.string(forKey: appliedKey) != fingerprint else { return }

    guard let start = routine.startComponents else {
      log.error("Argon sent a start time that is not HH:MM: \(routine.startAt)")
      return
    }

    let activity = ArgonRoutineActivity()
    let name = activity.getDeviceActivityName(from: profileId.uuidString)
    let center = DeviceActivityCenter()

    let endMinutes = (start.hour * 60 + start.minute + routine.windowMinutes) % (24 * 60)
    let schedule = DeviceActivitySchedule(
      intervalStart: DateComponents(hour: start.hour, minute: start.minute),
      intervalEnd: DateComponents(hour: endMinutes / 60, minute: endMinutes % 60),
      repeats: true
    )

    center.stopMonitoring([name])
    do {
      try center.startMonitoring(name, during: schedule)
      defaults.set(fingerprint, forKey: appliedKey)
      log.info("Argon routine armed for \(routine.startAt) (+\(routine.windowMinutes)m)")
    } catch {
      // Leave the fingerprint unset so the next refresh tries again rather than
      // believing a schedule is armed when none is.
      log.error("Could not arm the Argon routine: \(error.localizedDescription)")
    }
  }
}
