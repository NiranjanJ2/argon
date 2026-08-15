import FamilyControls
import SwiftData
import SwiftUI

struct SettingsView: View {
  @Environment(\.dismiss) private var dismiss
  @Environment(\.modelContext) private var context
  @EnvironmentObject var themeManager: ThemeManager
  @EnvironmentObject var requestAuthorizer: RequestAuthorizer
  @EnvironmentObject var strategyManager: StrategyManager
  @EnvironmentObject var argonBridge: ArgonBridge

  @State private var showResetBlockingStateAlert = false
  @State private var showDebugView = false
  /// ArgonOverride lives in UserDefaults, not in @Published state, so the view
  /// needs a nudge to re-read it after the switch is thrown.
  @State private var overrideTick = 0

  private var appVersion: String {
    Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String
      ?? "1.0"
  }

  private var apiToken: Binding<String> {
    Binding(
      get: { argonBridge.apiToken },
      set: { argonBridge.apiToken = $0 }
    )
  }

  /// The last thing on the page: kill any block, now, whatever Argon wants.
  ///
  /// This existed already but was only reachable from inside a running session,
  /// which is the wrong place for an escape hatch — the moment you need one is
  /// the moment you are least willing to hunt for it. It is deliberately a
  /// switch rather than a button: a block that ends is a state you are in for a
  /// while, and the switch shows you that state and how long is left.
  ///
  /// `ArgonOverride` is entirely on-device, so this works with no network, no
  /// gateway and no server. Telling the server is best effort on top — without
  /// it the reconciler would re-apply the block on its next poll, about twenty
  /// seconds later, and silently undo what you just did.
  @ViewBuilder
  private var emergencyRelease: some View {
    Section {
      Toggle(isOn: overrideBinding) {
        VStack(alignment: .leading, spacing: 3) {
          Text("Emergency release")
            .font(.headline)
            .foregroundStyle(.red)
          Text(overrideCaption)
            .font(.caption)
            .foregroundStyle(.secondary)
        }
      }
      .tint(.red)
    } footer: {
      Text(
        "Cancels any Screen Time block immediately and stops Argon re-applying "
          + "one for \(ArgonOverride.defaultMinutes) minutes. Works with no signal."
      )
    }
  }

  private var overrideBinding: Binding<Bool> {
    Binding(
      get: { ArgonOverride.isActive },
      set: { engaged in
        if engaged {
          ArgonOverride.engage(minutes: ArgonOverride.defaultMinutes)
          _ = strategyManager.applyArgonUnlock(context: context)
          argonBridge.reportEmergencyOverride(minutes: ArgonOverride.defaultMinutes)
        } else {
          ArgonOverride.clear()
          argonBridge.reportEmergencyOverride(minutes: 0)
        }
        overrideTick += 1
      }
    )
  }

  private var overrideCaption: String {
    guard let until = ArgonOverride.activeUntil else {
      return "Blocks are allowed to apply"
    }
    let minutes = max(1, Int(until.timeIntervalSinceNow / 60).advanced(by: 1))
    return "Blocking held off for \(minutes) more minute\(minutes == 1 ? "" : "s")"
  }

  var body: some View {
    NavigationStack {
      Form {
        Section {
          LabeledContent {
            TextField("https://argon.agentneon.dev", text: $argonBridge.serverURL)
              .keyboardType(.URL)
              .textInputAutocapitalization(.never)
              .autocorrectionDisabled()
              .multilineTextAlignment(.trailing)
          } label: {
            Label("Server", systemImage: "server.rack")
          }

          LabeledContent {
            SecureField("API token", text: apiToken)
              .textInputAutocapitalization(.never)
              .autocorrectionDisabled()
              .multilineTextAlignment(.trailing)
          } label: {
            Label("API token", systemImage: "key.fill")
          }

          LabeledContent {
            TextField("Argon Lockdown", text: $argonBridge.profileName)
              .multilineTextAlignment(.trailing)
          } label: {
            Label("Screen Time profile", systemImage: "shield.lefthalf.filled")
          }

          Button {
            argonBridge.connect()
          } label: {
            HStack {
              Label("Connect to Argon", systemImage: "bolt.horizontal.circle.fill")
              Spacer()
              Text(argonBridge.connectionState)
                .font(.caption)
                .foregroundStyle(
                  argonBridge.connectionState == "Connected" ? .green : .secondary
                )
            }
          }
          .disabled(!argonBridge.isConfigured)

          if !argonBridge.deviceToken.isEmpty {
            LabeledContent("This iPhone") {
              Text("••••\(argonBridge.deviceToken.suffix(8))")
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
            }
          }

          if let lastError = argonBridge.lastError {
            Text(lastError)
              .font(.caption)
              .foregroundStyle(.orange)
          }
        } header: {
          Text("Argon Connection")
        } footer: {
          Text(
            "This is the api.token from ~/.argon/config.json. The app reconciles to Argon's desired state directly; APNs wakes it for background changes."
          )
        }

        Section("Theme") {
          HStack {
            Image(systemName: "paintpalette.fill")
              .foregroundStyle(themeManager.themeColor)
              .font(.title3)

            VStack(alignment: .leading, spacing: 2) {
              Text("Appearance")
                .font(.headline)
              Text("Customize the look of your app")
                .font(.caption)
                .foregroundStyle(.secondary)
            }
          }
          .padding(.vertical, 8)

          Picker("Theme Color", selection: $themeManager.selectedColorName) {
            ForEach(ThemeManager.availableColors, id: \.name) { colorOption in
              HStack {
                Circle()
                  .fill(colorOption.color)
                  .frame(width: 20, height: 20)
                Text(colorOption.name)
              }
              .tag(colorOption.name)
            }
          }
          .onChange(of: themeManager.selectedColorName) { _, _ in
            UIImpactFeedbackGenerator(style: .medium).impactOccurred()
          }
        }

        AppIconPicker(selectionColor: themeManager.themeColor)

        Section("Help") {
          HStack {
            Text("Debug Mode")
              .foregroundColor(.primary)
            Spacer()
            Image(systemName: "chevron.right")
              .foregroundColor(.secondary)
              .font(.caption)
          }
          .onTapGesture {
            showDebugView = true
          }

          Link(destination: URL(string: "https://www.foqos.app/blocking-native-apps.html")!) {
            HStack {
              Text("Screen Time Help")
                .foregroundColor(.primary)
              Spacer()
              Image(systemName: "arrow.up.right.square")
                .foregroundColor(.secondary)
            }
          }

          if !strategyManager.isBlocking {
            Button {
              showResetBlockingStateAlert = true
            } label: {
              Text("Reset Blocking State")
                .foregroundColor(themeManager.themeColor)
            }
          }
        }

        Section("About") {
          HStack {
            Text("Version")
              .foregroundStyle(.primary)
            Spacer()
            Text("v\(appVersion)")
              .foregroundStyle(.secondary)
          }

          HStack {
            Text("Screen Time Access")
              .foregroundStyle(.primary)
            Spacer()
            HStack(spacing: 8) {
              Circle()
                .fill(requestAuthorizer.getAuthorizationStatus() == .approved ? .green : .red)
                .frame(width: 8, height: 8)
              Text(
                requestAuthorizer.getAuthorizationStatus() == .approved
                  ? "Authorized" : "Not Authorized"
              )
              .foregroundStyle(.secondary)
              .font(.subheadline)
            }
          }

          Link(destination: URL(string: "https://github.com/awaseem/foqos")!) {
            HStack {
              Text("Screen Time engine")
                .foregroundColor(.primary)
              Spacer()
              Text("Foqos")
                .foregroundColor(.secondary)
            }
          }
        }

        emergencyRelease
      }
      .navigationTitle("Settings")
      .toolbar {
        ToolbarItem(placement: .topBarLeading) {
          Button(action: { dismiss() }) {
            Image(systemName: "xmark")
          }
          .accessibilityLabel("Close")
        }
      }
      .alert("Reset Blocking State", isPresented: $showResetBlockingStateAlert) {
        Button("Cancel", role: .cancel) {}
        Button("Reset", role: .destructive) {
          strategyManager.resetBlockingState(context: context)
        }
      } message: {
        Text(
          "This will clear all app restrictions and remove any ghost schedules. Only use this if you're locked out and no profile is active."
        )
      }
      .sheet(isPresented: $showDebugView) {
        DebugView()
      }
    }
  }
}

#Preview {
  SettingsView()
    .environmentObject(ThemeManager.shared)
    .environmentObject(RequestAuthorizer())
    .environmentObject(StrategyManager.shared)
    .environmentObject(ArgonBridge.shared)
    .modelContainer(for: BlockedProfiles.self, inMemory: true)
}
