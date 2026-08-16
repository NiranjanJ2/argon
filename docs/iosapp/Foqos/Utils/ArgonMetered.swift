import DeviceActivity
import FamilyControls
import Foundation
import ManagedSettings

/// Argon's own metered mode: a real usage budget, enforced by Screen Time.
///
/// This replaces an adaptation of foqos's soft-unblock grants, which modelled a
/// different thing. A grant is *one unblock window per period*: you tap a
/// shielded app, it opens for fifteen minutes whether you use it or not, and
/// closing it after thirty seconds still spends the whole allowance. What was
/// asked for is fifteen minutes of *use* per hour, which is the thing
/// `DeviceActivityEvent` thresholds exist to measure.
///
/// So the shape is different. There is no session, no grant store, and no
/// button on the shield: apps are simply open until the budget is spent, and
/// shielded once it is, until the hour turns and the budget refills. Nothing
/// has to be tapped, and time not spent is not lost.
///
/// The whole mechanism lives in Screen Time rather than in the app. Once
/// monitoring starts, the shield goes up from the extension even if the app is
/// dead and the server is unreachable — where the grant model needed the app to
/// have run recently enough to have opened a session.
enum ArgonMetered {
  static let activityName = DeviceActivityName("ArgonMetered")
  static let eventName = DeviceActivityEvent.Name("ArgonAllowanceSpent")

  /// A store of Argon's own, so raising and clearing this shield cannot
  /// interfere with the one foqos uses for ordinary blocks.
  static let store = ManagedSettingsStore(named: .argonMetered)

  /// Windows Screen Time can actually repeat.
  ///
  /// A `DeviceActivitySchedule` whose components are minutes repeats hourly;
  /// one with hours and minutes repeats daily. There is no single schedule that
  /// repeats every six hours — that needs four schedules and four events — so
  /// rather than pretend, only the two the system supports are offered.
  enum Window: Int {
    case hourly = 1
    case daily = 24

    var schedule: DeviceActivitySchedule {
      switch self {
      case .hourly:
        return DeviceActivitySchedule(
          intervalStart: DateComponents(minute: 0),
          intervalEnd: DateComponents(minute: 59),
          repeats: true
        )
      case .daily:
        return DeviceActivitySchedule(
          intervalStart: DateComponents(hour: 0, minute: 0),
          intervalEnd: DateComponents(hour: 23, minute: 59),
          repeats: true
        )
      }
    }
  }

  /// Begin metering *selection* to *minutes* of use per window.
  ///
  /// Idempotent by necessity: the reconciler runs on every status poll, and
  /// restarting monitoring resets the interval, which would refill the budget
  /// every twenty seconds and make the limit meaningless. Already monitoring on
  /// the same terms is left strictly alone.
  static func start(
    minutes: Int,
    window: Window,
    selection: FamilyActivitySelection
  ) throws {
    let center = DeviceActivityCenter()
    let terms = Terms(minutes: minutes, window: window)

    if center.activities.contains(activityName), Terms.current == terms {
      return
    }

    center.stopMonitoring([activityName])

    let event = DeviceActivityEvent(
      applications: selection.applicationTokens,
      categories: selection.categoryTokens,
      webDomains: selection.webDomainTokens,
      threshold: DateComponents(minute: max(1, minutes))
    )
    // Written before monitoring starts: the threshold can fire as soon as
    // monitoring begins, and the extension shields whatever it finds here.
    SharedData.setArgonMeteredSelection(selection)
    try center.startMonitoring(
      activityName,
      during: window.schedule,
      events: [eventName: event]
    )
    terms.save()
    // The budget starts full: whatever was shielded from a previous window
    // should not carry into a mode the user just turned on.
    clearShield()
  }

  static func stop() {
    DeviceActivityCenter().stopMonitoring([activityName])
    Terms.clear()
    clearShield()
  }

  static var isMonitoring: Bool {
    DeviceActivityCenter().activities.contains(activityName)
  }

  // MARK: - Shield

  /// Called from the monitor extension when the budget runs out.
  static func raiseShield() {
    guard let selection = SharedData.argonMeteredSelection() else { return }
    store.shield.applications =
      selection.applicationTokens.isEmpty ? nil : selection.applicationTokens
    store.shield.applicationCategories =
      selection.categoryTokens.isEmpty
      ? nil : ShieldSettings.ActivityCategoryPolicy.specific(selection.categoryTokens)
    store.shield.webDomains =
      selection.webDomainTokens.isEmpty ? nil : selection.webDomainTokens
    Terms.markSpent(true)
  }

  /// Called at the start of each window: the budget refills.
  static func clearShield() {
    store.shield.applications = nil
    store.shield.applicationCategories = nil
    store.shield.webDomains = nil
    Terms.markSpent(false)
  }

  // MARK: - Terms

  /// What is being metered, shared with the extension and readable for logs.
  struct Terms: Codable, Equatable {
    var minutes: Int
    var windowHours: Int
    var spent: Bool = false

    init(minutes: Int, window: Window) {
      self.minutes = minutes
      self.windowHours = window.rawValue
    }

    static func == (a: Terms, b: Terms) -> Bool {
      a.minutes == b.minutes && a.windowHours == b.windowHours
    }

    private static let key = "argon.metered.terms"
    private static var suite: UserDefaults? {
      UserDefaults(suiteName: "group.com.niranjanj.argon")
    }

    static var current: Terms? {
      guard let data = suite?.data(forKey: key) else { return nil }
      return try? JSONDecoder().decode(Terms.self, from: data)
    }

    func save() {
      guard let data = try? JSONEncoder().encode(self) else { return }
      Self.suite?.set(data, forKey: Self.key)
    }

    static func clear() { suite?.removeObject(forKey: key) }

    static func markSpent(_ spent: Bool) {
      guard var terms = current else { return }
      terms.spent = spent
      terms.save()
    }
  }
}

extension ManagedSettingsStore.Name {
  static let argonMetered = Self("argonMetered")
}

extension ArgonMetered {
  /// The apps he chose to meter at the weekend, if he chose any.
  ///
  /// Separate from the lockdown profile on purpose. Locking in and taking a
  /// lighter weekend are different intents over different apps: the block list
  /// is everything that could distract him, while the metered list is usually
  /// the two or three things he actually wants a little of. Sharing one list
  /// meant weekend mode rationed his whole phone.
  ///
  /// Also separate from the selection the extension reads, which is whatever is
  /// *currently* being metered. Editing this while metered mode is off must not
  /// change what a running shield applies to.
  static var configuredApps: FamilyActivitySelection? {
    get {
      guard let data = configuredSuite?.data(forKey: configuredKey) else { return nil }
      return try? JSONDecoder().decode(FamilyActivitySelection.self, from: data)
    }
    set {
      guard let newValue, let data = try? JSONEncoder().encode(newValue) else {
        configuredSuite?.removeObject(forKey: configuredKey)
        return
      }
      configuredSuite?.set(data, forKey: configuredKey)
    }
  }

  /// True when he has actually picked something, rather than left it empty.
  static var hasConfiguredApps: Bool {
    guard let selection = configuredApps else { return false }
    return !selection.applicationTokens.isEmpty
      || !selection.categoryTokens.isEmpty
      || !selection.webDomainTokens.isEmpty
  }

  private static var configuredSuite: UserDefaults? {
    UserDefaults(suiteName: "group.com.niranjanj.argon")
  }
  private static let configuredKey = "argon.weekend.selection"
}

extension SharedData {
  /// What metered mode is metering, stored where the monitor extension can
  /// reach it.
  ///
  /// Kept separately from the profile snapshot on purpose: the extension runs
  /// when the app does not, and reading it out of a foqos profile would tie
  /// this shield to whether that profile still exists and still names the same
  /// apps. Argon writes what it is metering, once, and the extension shields
  /// exactly that.
  private static var meteredSuite: UserDefaults? {
    UserDefaults(suiteName: "group.com.niranjanj.argon")
  }
  private static let meteredSelectionKey = "argon.metered.selection"

  static func setArgonMeteredSelection(_ selection: FamilyActivitySelection?) {
    guard let selection, let data = try? JSONEncoder().encode(selection) else {
      meteredSuite?.removeObject(forKey: meteredSelectionKey)
      return
    }
    meteredSuite?.set(data, forKey: meteredSelectionKey)
  }

  static func argonMeteredSelection() -> FamilyActivitySelection? {
    guard let data = meteredSuite?.data(forKey: meteredSelectionKey) else { return nil }
    return try? JSONDecoder().decode(FamilyActivitySelection.self, from: data)
  }
}
