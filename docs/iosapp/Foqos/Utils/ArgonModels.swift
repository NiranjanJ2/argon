import Foundation

struct ArgonDesiredMode: Codable, Equatable {
  let mode: String
  let version: Int
  let since: String?
  let expiresAt: String?
  let allowEarlyEnd: Bool
  let reason: String

  enum CodingKeys: String, CodingKey {
    case mode
    case version
    case since
    case expiresAt = "expires_at"
    case allowEarlyEnd = "allow_early_end"
    case reason
  }

  var expiryDate: Date? {
    guard let expiresAt else { return nil }
    return ArgonServerDate.parse(expiresAt)
  }

  var hasValidHardExpiry: Bool {
    mode == "off" || expiryDate != nil
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
