import Foundation

/// One air conditioner as the server reports it.
///
/// Temperatures arrive in celsius because that is what the protocol carries,
/// while the unit itself is set to display fahrenheit — so anything shown to
/// him converts, or the number on screen disagrees with the number on the wall.
struct ArgonACUnit: Codable, Identifiable, Equatable {
  let mac: String
  let name: String
  let on: Bool
  let mode: String
  let targetC: Int?
  let roomC: Int?
  let fan: Int?
  let reachable: Bool?

  var id: String { mac }

  enum CodingKeys: String, CodingKey {
    case mac, name, on, mode, fan, reachable
    case targetC = "target_c"
    case roomC = "room_c"
  }

  init(from decoder: Decoder) throws {
    let c = try decoder.container(keyedBy: CodingKeys.self)
    mac = (try? c.decode(String.self, forKey: .mac)) ?? ""
    name = (try? c.decode(String.self, forKey: .name)) ?? ""
    on = (try? c.decode(Bool.self, forKey: .on)) ?? false
    mode = (try? c.decode(String.self, forKey: .mode)) ?? "unknown"
    targetC = try? c.decode(Int.self, forKey: .targetC)
    roomC = try? c.decode(Int.self, forKey: .roomC)
    fan = try? c.decode(Int.self, forKey: .fan)
    reachable = try? c.decode(Bool.self, forKey: .reachable)
  }

  var displayName: String { name.isEmpty ? "Air conditioner" : name }

  static func fahrenheit(_ celsius: Int?) -> Int? {
    guard let celsius else { return nil }
    return Int((Double(celsius) * 9.0 / 5.0 + 32.0).rounded())
  }

  var targetF: Int? { Self.fahrenheit(targetC) }
  var roomF: Int? { Self.fahrenheit(roomC) }
}

struct ArgonACResponse: Codable {
  let units: [ArgonACUnit]
}
