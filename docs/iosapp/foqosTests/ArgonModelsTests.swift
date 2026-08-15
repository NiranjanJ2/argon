import XCTest

@testable import foqos

final class ArgonModelsTests: XCTestCase {
  func testDecodesDesiredState() throws {
    let data = Data(
      """
      {
        "mode": "lock_in",
        "version": 41,
        "since": "2026-07-30T19:04:11-07:00",
        "expires_at": "2026-07-30T21:00:00-07:00",
        "allow_early_end": false,
        "reason": "Chem pset"
      }
      """.utf8
    )

    let desired = try JSONDecoder().decode(ArgonDesiredMode.self, from: data)

    XCTAssertEqual(desired.mode, "lock_in")
    XCTAssertEqual(desired.version, 41)
    XCTAssertEqual(desired.reason, "Chem pset")
    XCTAssertFalse(desired.allowEarlyEnd)
    XCTAssertNotNil(desired.expiryDate)
  }

  func testParsesSixDigitPythonExpiryTimestamp() throws {
    let data = Data(
      """
      {
        "mode": "lock_in",
        "version": 42,
        "since": "2026-07-30T21:01:02.123456-07:00",
        "expires_at": "2026-07-30T22:01:02.123456-07:00",
        "allow_early_end": true,
        "reason": "Finish the session"
      }
      """.utf8
    )

    let desired = try JSONDecoder().decode(ArgonDesiredMode.self, from: data)

    XCTAssertNotNil(desired.expiryDate)
    XCTAssertTrue(desired.hasValidHardExpiry)
    XCTAssertEqual(desired.mode, "lock_in")
  }

  func testLockWithMalformedExpiryFailsHardExpiryValidation() throws {
    let data = Data(
      """
      {
        "mode": "lock_in",
        "version": 43,
        "since": "2026-07-30T21:01:02-07:00",
        "expires_at": "not-a-date",
        "allow_early_end": true,
        "reason": "Broken payload"
      }
      """.utf8
    )

    let desired = try JSONDecoder().decode(ArgonDesiredMode.self, from: data)

    XCTAssertNil(desired.expiryDate)
    XCTAssertFalse(desired.hasValidHardExpiry)
  }

  func testStatusDecodesWhenActualStateIsMissing() throws {
    let data = Data(
      """
      {
        "mode": "idle",
        "ios": {
          "desired": {
            "mode": "off",
            "version": 3,
            "since": "2026-07-30T21:01:02-07:00",
            "expires_at": null,
            "allow_early_end": true,
            "reason": ""
          }
        }
      }
      """.utf8
    )

    let status = try JSONDecoder().decode(ArgonStatusResponse.self, from: data)

    XCTAssertEqual(status.mode, "idle")
    XCTAssertEqual(status.ios?.desired?.version, 3)
    XCTAssertNil(status.ios?.actual)
  }

  func testMalformedIOSStateDoesNotTakeStatusOffline() throws {
    let data = Data(
      """
      {
        "mode": "working",
        "current_task": "Review",
        "ios": {
          "desired": { "mode": "lock_in" },
          "actual": { "version": "not-an-integer" }
        }
      }
      """.utf8
    )

    let status = try JSONDecoder().decode(ArgonStatusResponse.self, from: data)

    XCTAssertEqual(status.mode, "working")
    XCTAssertEqual(status.currentTask, "Review")
    XCTAssertNil(status.ios?.desired)
    XCTAssertNil(status.ios?.actual)
  }

  func testDecodesSharedTaskDashboard() throws {
    let data = Data(
      """
      {
        "tasks": [{
          "id": "chem-1",
          "title": "Finish chemistry set",
          "done": false,
          "priority": "high",
          "source": "manual",
          "subject": "CHEM 30A",
          "notes": null,
          "due": "2026-07-31",
          "classroom_id": null,
          "time_estimate_min": 45,
          "time_actual_min": 12,
          "started_at": "2026-07-31T08:00:00-07:00"
        }],
        "state": {
          "mode": "working",
          "current_task": "Finish chemistry set",
          "work_session_minutes": 12,
          "lock_in_minutes": 0
        }
      }
      """.utf8
    )

    let dashboard = try JSONDecoder().decode(ArgonTasksResponse.self, from: data)

    XCTAssertEqual(dashboard.tasks.first?.title, "Finish chemistry set")
    XCTAssertEqual(dashboard.tasks.first?.timeEstimateMinutes, 45)
    XCTAssertTrue(dashboard.tasks.first?.isStarted == true)
    XCTAssertNotNil(dashboard.tasks.first?.dueDay)
    XCTAssertEqual(dashboard.state.currentTask, "Finish chemistry set")
  }

  func testNullTaskDurationsDecodeAsZero() throws {
    let data = Data(
      """
      {
        "tasks": [],
        "state": {
          "mode": "idle",
          "current_task": null,
          "work_session_minutes": null,
          "lock_in_minutes": null
        }
      }
      """.utf8
    )

    let dashboard = try JSONDecoder().decode(ArgonTasksResponse.self, from: data)

    XCTAssertEqual(dashboard.state.workSessionMinutes, 0)
    XCTAssertEqual(dashboard.state.lockInMinutes, 0)
  }
}
