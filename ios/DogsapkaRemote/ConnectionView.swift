import SwiftUI

struct ConnectionView: View {
    @EnvironmentObject var store: RemoteStore
    @State private var confirmUnpair = false

    var body: some View {
        NavigationStack {
            List {
                ErrorBanner().listRowBackground(Color.clear)
                Section("Számítógép") {
                    LabeledContent("Állapot") {
                        Label(store.online ? "Kapcsolódva" : "Nem elérhető",
                              systemImage: store.online ? "checkmark.circle.fill" : "wifi.slash")
                            .foregroundStyle(store.online ? Theme.green : Theme.amber)
                    }
                    if let c = store.connection {
                        LabeledContent("Cím", value: "\(c.host):\(c.port)")
                    }
                    if let s = store.status {
                        LabeledContent("Verzió", value: s.version)
                        LabeledContent("Élő munkamenetek", value: "\(s.liveSessions)")
                        LabeledContent("Figyelt streamerek", value: "\(s.monitored)/\(s.streamers)")
                        LabeledContent("Feldolgozási sor", value: s.queuePaused ? "szünetel" : "\(s.queuePending) vár")
                        LabeledContent("Szabad hely", value: String(format: "%.1f GB", s.diskFreeGb))
                    }
                }
                Section {
                    Button("Leválasztás", role: .destructive) { confirmUnpair = true }
                } footer: {
                    Text("A számítógépen a Beállítások → Távirányító részben is letilthatod ezt a telefont.")
                }
                Section {
                    LabeledContent("App verzió",
                                   value: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "")
                }
            }
            .navigationTitle("Kapcsolat")
            .refreshable { await store.refresh(.connection) }
            .onAppear { store.startPolling(.connection) }
            .onDisappear { store.stopPolling() }
            .confirmationDialog("Leválasztod a telefont a számítógépről?", isPresented: $confirmUnpair,
                                titleVisibility: .visible) {
                Button("Leválasztás", role: .destructive) { store.unpair() }
            }
        }
    }
}
