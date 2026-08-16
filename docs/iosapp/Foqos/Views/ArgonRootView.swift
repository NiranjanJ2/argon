import SwiftUI

struct ArgonRootView: View {
  private enum Tab: Hashable {
    case dashboard
    case chat
    case focus
    case air
  }

  @Environment(\.scenePhase) private var scenePhase
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
    .toolbarBackground(ArgonPalette.canvasLifted.opacity(0.96), for: .tabBar)
    .toolbarBackground(.visible, for: .tabBar)
    .onAppear {
      bridge.startMonitoring()
      Task { await bridge.refreshTasks() }
    }
    .onChange(of: scenePhase) { _, phase in
      switch phase {
      case .active:
        bridge.startMonitoring()
        Task { await bridge.refreshTasks() }
      case .background:
        bridge.stopMonitoring()
      default:
        break
      }
    }
  }
}
