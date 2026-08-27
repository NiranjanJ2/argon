import Foundation
import Testing

@testable import foqos

struct ArgonRoutineTests {
  private func decode(_ json: String) throws -> ArgonRoutine {
    try JSONDecoder().decode(ArgonRoutine.self, from: Data(json.utf8))
  }

  @Test func itReadsWhatTheServerSends() throws {
    let routine = try decode(
      """
      {"start_at":"20:00","chosen":true,"default_start":"18:00",
       "planned_today":true,"school_nights":[6,0,1,2,3],
       "window_minutes":90,"warning_minutes":30}
      """)

    #expect(routine.startAt == "20:00")
    #expect(routine.chosen)
    #expect(routine.startComponents?.hour == 20)
    #expect(routine.startComponents?.minute == 0)
  }

  @Test func aStartTimeThatIsNotAClockIsRefused() throws {
    // Better to arm nothing than to arm a schedule at hour 99: an interval the
    // device rejects leaves him with no block and no way to tell.
    for bad in ["", "20", "8pm", "24:00", "20:60", "-1:00"] {
      let routine = try decode(
        """
        {"start_at":"\(bad)","chosen":false,"default_start":"18:00",
         "planned_today":false,"school_nights":[6,0,1,2,3],
         "window_minutes":90,"warning_minutes":30}
        """)
      #expect(routine.startComponents == nil, "\(bad) should not parse")
    }
  }

  @Test func aServerWithoutARoutineStillDecodes() throws {
    // An older server must not make the whole status payload undecodable and
    // take the shield down with it.
    let status = try JSONDecoder().decode(
      ArgonStatusResponse.self,
      from: Data(#"{"mode":"idle"}"#.utf8))

    #expect(status.routine == nil)
    #expect(status.mode == "idle")
  }

  @Test func schoolNightsUsePythonWeekdayNumbers() {
    // Calendar.weekday is 1=Sunday…7=Saturday; the server counts 0=Monday…6=Sunday.
    // Getting this backwards locks the phone on Saturday and leaves Monday open.
    ArgonRoutineSettings.save(schoolNights: [6, 0, 1, 2, 3])
    var components = DateComponents(year: 2026, month: 8, day: 27)  // Thursday
    let calendar = Calendar(identifier: .gregorian)

    #expect(ArgonRoutineSettings.isSchoolNightToday(now: calendar.date(from: components)!))

    components.day = 28  // Friday
    #expect(!ArgonRoutineSettings.isSchoolNightToday(now: calendar.date(from: components)!))

    components.day = 29  // Saturday
    #expect(!ArgonRoutineSettings.isSchoolNightToday(now: calendar.date(from: components)!))

    components.day = 30  // Sunday - a school day follows it
    #expect(ArgonRoutineSettings.isSchoolNightToday(now: calendar.date(from: components)!))
  }
}
