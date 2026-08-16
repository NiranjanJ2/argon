//
//  DeviceActivityMonitorExtension.swift
//  FoqosDeviceMonitor
//
//  Created by Ali Waseem on 2025-05-27.
//

import DeviceActivity
import ManagedSettings
import OSLog

private let log = Logger(
  subsystem: "com.foqos.monitor",
  category: "DeviceActivity"
)

// Optionally override any of the functions below.
// Make sure that your class name matches the NSExtensionPrincipalClass in your Info.plist.
class DeviceActivityMonitorExtension: DeviceActivityMonitor {
  private let appBlocker = AppBlockerUtil()

  override init() {
    super.init()
  }

  override func intervalDidStart(for activity: DeviceActivityName) {
    super.intervalDidStart(for: activity)

    log.info("intervalDidStart for activity: \(activity.rawValue)")

    // A new window: the metered budget refills, so whatever was shielded when
    // it ran out is released. This is the only thing that lifts that shield —
    // deliberately, so it survives the app being killed and the server being
    // unreachable, which is where the previous grant-based design fell over.
    if activity == ArgonMetered.activityName {
      ArgonMetered.clearShield()
      return
    }

    TimerActivityUtil.startTimerActivity(for: activity)
  }

  override func intervalDidEnd(for activity: DeviceActivityName) {
    super.intervalDidEnd(for: activity)

    log.info("intervalDidEnd for activity: \(activity.rawValue)")
    guard activity != ArgonMetered.activityName else { return }
    TimerActivityUtil.stopTimerActivity(for: activity)
  }

  /// The budget is spent: shield until the window turns over.
  ///
  /// This measures actual usage, not elapsed time — thirty seconds in an app
  /// costs thirty seconds, where the grant model this replaced spent the whole
  /// fifteen minutes the moment the app was opened.
  override func eventDidReachThreshold(
    _ event: DeviceActivityEvent.Name,
    activity: DeviceActivityName
  ) {
    super.eventDidReachThreshold(event, activity: activity)

    log.info("threshold reached: \(event.rawValue) in \(activity.rawValue)")
    guard activity == ArgonMetered.activityName, event == ArgonMetered.eventName else {
      return
    }
    ArgonMetered.raiseShield()
  }
}
