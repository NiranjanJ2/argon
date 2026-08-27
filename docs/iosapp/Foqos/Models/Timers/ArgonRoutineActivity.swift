import DeviceActivity
import Foundation
import OSLog

private let log = Logger(subsystem: "com.foqos.monitor", category: "ArgonRoutine")

/// The evening block, kept by the device instead of by the server.
///
/// Argon used to publish a desired mode and wait for the phone to notice. The
/// phone only notices while the app is foregrounded — `ArgonBridge` reconciles
/// on a `Timer.scheduledTimer` that iOS suspends the moment the app leaves the
/// screen — so a block published at 20:00 on 2026-08-26 was still unapplied
/// four hours later, and every APNs push since 08/20 had been rejected anyway.
///
/// A `DeviceActivitySchedule` runs in this extension, with the app closed, the
/// server down and the network off. That is the whole reason the routine lives
/// here now.
///
/// The schedule repeats daily and the weekday is filtered at fire time rather
/// than expressed as five weekly schedules: `TimerActivityUtil` parses an
/// activity name as `type:profileId`, so five names would all have to be the
/// same one. One daily activity plus this check is the version that fits.
class ArgonRoutineActivity: TimerActivity {
  static var id: String = "ArgonRoutineActivity"

  private let appBlocker = AppBlockerUtil()

  func getDeviceActivityName(from profileId: String) -> DeviceActivityName {
    return DeviceActivityName(rawValue: "\(Self.id):\(profileId)")
  }

  func start(for profile: SharedData.ProfileSnapshot) {
    let profileId = profile.id.uuidString

    guard ArgonRoutineSettings.isSchoolNightToday() else {
      log.info("Argon routine for \(profileId): not a school night, standing down")
      return
    }

    // He filled in the form and started something before the block came round.
    // Locking him out of a session he is already in is the one outcome worse
    // than not locking at all.
    if let existing = SharedData.getActiveSharedSession() {
      log.info("Argon routine for \(profileId): a session is already running")
      if existing.blockedProfileId != profile.id {
        return
      }
      return
    }

    log.info("Argon routine for \(profileId): starting the evening block")
    SharedData.createSessionForSchedular(for: profile.id)
    appBlocker.activateRestrictions(for: profile)
  }

  func stop(for profile: SharedData.ProfileSnapshot) {
    let profileId = profile.id.uuidString

    guard let activeSession = SharedData.getActiveSharedSession() else {
      log.info("Argon routine for \(profileId): nothing running to stop")
      return
    }
    guard activeSession.blockedProfileId == profile.id else {
      log.info("Argon routine for \(profileId): a different profile owns the session")
      return
    }

    // The interval end is the hard stop. It is applied on-device precisely so a
    // dead server cannot strand him behind a shield.
    appBlocker.deactivateRestrictions()
    SharedData.endActiveSharedSession()
  }
}

/// What the monitor extension needs to know, in the shared app group.
///
/// The extension is a separate process with no access to the app's memory or to
/// the network, so the weekday filter has to be written down where it can read
/// it. Defaults, not SwiftData: it is two values and a list of integers.
enum ArgonRoutineSettings {
  private static let suite = UserDefaults(suiteName: "group.com.niranjanj.argon")
  private static let schoolNightsKey = "argon.routine.schoolNights"

  /// Python weekday numbers, so the server's list travels unchanged.
  private static let defaultSchoolNights = [6, 0, 1, 2, 3]

  static func save(schoolNights: [Int]) {
    suite?.set(schoolNights, forKey: schoolNightsKey)
  }

  static func isSchoolNightToday(now: Date = Date()) -> Bool {
    let nights = suite?.array(forKey: schoolNightsKey) as? [Int] ?? defaultSchoolNights
    // Calendar.weekday is 1=Sunday…7=Saturday; Python is 0=Monday…6=Sunday.
    let weekday = Calendar.current.component(.weekday, from: now)
    return nights.contains((weekday + 5) % 7)
  }
}
