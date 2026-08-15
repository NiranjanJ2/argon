import Foundation

/// A metered block: apps stay shielded, but tapping one buys `minutes` of
/// access, `perHours` times over. Absent for an ordinary hard block.
struct ArgonAllowance: Codable, Equatable {
  let minutes: Int
  let perHours: Int

  enum CodingKeys: String, CodingKey {
    case minutes
    case perHours = "per_hours"
  }
}

struct ArgonDesiredMode: Codable, Equatable {
  let mode: String
  let version: Int
  let since: String?
  let expiresAt: String?
  let allowEarlyEnd: Bool
  let reason: String
  /// Optional so a server that predates metered modes still decodes. A failed
  /// decode here would blank the whole status payload and read as "Offline".
  let allowance: ArgonAllowance?

  enum CodingKeys: String, CodingKey {
    case mode
    case version
    case since
    case expiresAt = "expires_at"
    case allowEarlyEnd = "allow_early_end"
    case reason
    case allowance
  }

  var expiryDate: Date? {
    guard let expiresAt else { return nil }
    return ArgonServerDate.parse(expiresAt)
  }

  var isMetered: Bool { allowance != nil }

  /// A hard block with no expiry is a trap: if the server dies while it is on,
  /// nothing on the phone ever lifts it, so the reconciler refuses one outright.
  /// A metered block is not a trap — there is always a way through it — so it
  /// is allowed to run open-ended, which is what "all weekend" needs.
  var hasValidHardExpiry: Bool {
    mode == "off" || isMetered || expiryDate != nil
  }
}

private enum ArgonServerDate {
  static func parse(_ value: String) -> Date? {
    let fractional = ISO8601DateFormatter()
    fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    if let date = fractional.date(from: value) {
      return date
    }

    // ISO8601DateFormatter behavior for sub-second precision has varied across
    // OS releases. Normalize Python's 1-9 digit fractions to milliseconds so
    // an expiry can never become an accidental open-ended lock.
    if let fractionRange = value.range(
      of: #"\.\d+(?=Z|[+-]\d{2}:\d{2}$)"#,
      options: .regularExpression
    ) {
      let fraction = value[fractionRange].dropFirst()
      let milliseconds = String(fraction.prefix(3)).padding(
        toLength: 3,
        withPad: "0",
        startingAt: 0
      )
      let normalized = value.replacingCharacters(
        in: fractionRange,
        with: ".\(milliseconds)"
      )
      if let date = fractional.date(from: normalized) {
        return date
      }
    }

    return ISO8601DateFormatter().date(from: value)
  }
}
