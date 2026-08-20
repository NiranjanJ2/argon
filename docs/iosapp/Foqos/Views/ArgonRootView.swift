import SwiftUI

struct ArgonRootView: View {
  private enum Tab: Hashable {
    case dashboard
    case chat
    case focus
    case air
  }

  @Environment(\.scenePhase) private var scenePhase
  /// Set only when the server says today has not been planned yet. The server
  /// owns that decision so the answer survives reinstalling the app, and so
  /// planning on one surface closes it on the others.
  @State private var plannerPayload: ArgonPlannerPayload?
  @EnvironmentObject private var bridge: ArgonBridge
  @State private var selection: Tab

  init(defaults: UserDefaults = .standard, processInfo: ProcessInfo = .processInfo) {
    let hasSeenIntro = defaults.object(forKey: "showIntroScreen") as? Bool == false
    var initialTab: Tab = hasSeenIntro ? .dashboard : .focus
    #if DEBUG
      switch processInfo.environment["ARGON_INITIAL_TAB"] {
      case "chat": initialTab = .chat
      case "focus": initialTab = .focus
      case "dashboard": initialTab = .dashboard
      default: break
      }
    #endif
    _selection = State(initialValue: initialTab)
  }

  /// Ask the server whether the planning screen is due, and open it if so.
  ///
  /// Nothing is shown when there is nothing to decide: no overdue work and no
  /// invisible-class prompts means the sheet would be a dialog he has to
  /// dismiss to reach his own task list.
  private func offerPlannerIfDue() async {
    guard plannerPayload == nil else { return }
    guard let payload = await bridge.fetchPlanner() else { return }
    if payload.needed && payload.hasAnythingToDecide {
      plannerPayload = payload
    }
  }

  var body: some View {
    TabView(selection: $selection) {
      ArgonDashboardView()
        .tag(Tab.dashboard)
        .tabItem {
          Label("Dashboard", systemImage: "square.grid.2x2.fill")
        }

      ArgonChatView()
        .tag(Tab.chat)
        .tabItem {
          Label("Chat", systemImage: "bubble.left.and.bubble.right.fill")
        }

      HomeView()
        .tag(Tab.focus)
        .tabItem {
          Label("Focus", systemImage: "shield.lefthalf.filled")
        }

      // Nothing to do with the assistant — it is here because this is the app
      // he already has open, and the server it talks to is the machine on the
      // same LAN as the air conditioner.
      ArgonACView()
        .tag(Tab.air)
        .tabItem {
          Label("Air", systemImage: "snowflake")
        }
    }
    .tint(ArgonPalette.iceBlue)
    .sheet(item: $plannerPayload) { payload in
      ArgonPlannerView(payload: payload)
        .environmentObject(bridge)
        .presentationDetents([.large])
    }
    .toolbarBackground(ArgonPalette.canvasLifted.opacity(0.96), for: .tabBar)
    .toolbarBackground(.visible, for: .tabBar)
    .onAppear {
      bridge.startMonitoring()
      Task {
        await bridge.refreshTasks()
        await offerPlannerIfDue()
      }
    }
    .onChange(of: scenePhase) { _, phase in
      switch phase {
      case .active:
        bridge.startMonitoring()
        Task {
          await bridge.refreshTasks()
          await offerPlannerIfDue()
        }
      case .background:
        bridge.stopMonitoring()
      default:
        break
      }
    }
  }
}
