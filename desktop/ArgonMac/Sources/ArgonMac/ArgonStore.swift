import Foundation
import SwiftUI

/// Polls Argon and holds the one snapshot every view reads.
@MainActor
final class ArgonStore: ObservableObject {
  @Published private(set) var snapshot = ArgonSnapshot.empty
  @Published private(set) var busyIDs: Set<String> = []

  private let client = ArgonClient()
  private var timer: Task<Void, Never>?

  /// Ten seconds. The old SwiftBar plugin ran at the same cadence, and it is
  /// slow enough that a laptop on battery does not notice while still feeling
  /// immediate after a tap — which also triggers a refresh directly.
  private let interval: Duration = .seconds(10)

  func start() {
    guard timer == nil else { return }
    timer = Task { [weak self] in
      while !Task.isCancelled {
        await self?.refresh()
        try? await Task.sleep(for: self?.interval ?? .seconds(10))
      }
    }
  }

  func stop() {
    timer?.cancel()
    timer = nil
  }

  func refresh() async {
    snapshot = await client.fetch()
  }

  /// Optimism is deliberate: the row dims immediately, and the refresh that
  /// follows replaces it with whatever the server actually thinks. Nothing has
  /// to be rolled back, because nothing was written locally to roll back.
  func act(_ task: ArgonTask, action: String) {
    guard !busyIDs.contains(task.id) else { return }
    busyIDs.insert(task.id)
    Task {
      defer { busyIDs.remove(task.id) }
      try? await client.act(taskId: task.id, action: action)
      await refresh()
    }
  }

  func answer(_ item: ArgonInboxItem, action: ArgonInboxAction) {
    let marker = "\(item.id):\(action.id)"
    guard !busyIDs.contains(marker) else { return }
    busyIDs.insert(marker)
    Task {
      defer { busyIDs.remove(marker) }
      if let taskId = action.taskId, action.action == "start" || action.action == "complete" {
        try? await client.act(taskId: taskId, action: action.action)
      }
      await client.answer(itemId: item.id, action: action.action)
      await refresh()
    }
  }

  /// What sits in the menu bar. Deliberately short — a menu bar title that
  /// grows with the task title pushes every other item off a small screen.
  var barTitle: String {
    if snapshot.hasNeverSynced { return "—" }
    if !snapshot.waiting.isEmpty { return "\(snapshot.dueNow.count)·\(snapshot.waiting.count)?" }
    return "\(snapshot.dueNow.count)"
  }

  var barIcon: String {
    if snapshot.error != nil && snapshot.hasNeverSynced { return "exclamationmark.triangle" }
    if snapshot.runningTask != nil { return "circle.inset.filled" }
    if snapshot.shielded { return "shield.lefthalf.filled" }
    return "circle"
  }
}
