import AVFoundation
import SwiftUI

struct PairView: View {
    @EnvironmentObject var store: RemoteStore
    @State private var scanning = false
    @State private var address = ""
    @State private var code = ""
    @State private var busy = false
    @State private var message: String?

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 22) {
                    Image(systemName: "play.rectangle.fill")
                        .font(.system(size: 64))
                        .foregroundStyle(LinearGradient(colors: [Theme.purple, Theme.cyan],
                                                        startPoint: .topLeading, endPoint: .bottomTrailing))
                        .padding(.top, 30)
                    Text("DOGSAPKA távirányító").font(.title.bold())
                    Text("A számítógépen: Beállítások → Távirányító (iPhone) → kapcsold be, majd „iPhone párosítása”. "
                         + "A telefon legyen ugyanazon a Wi-Fi-n.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)

                    Button {
                        scanning = true
                    } label: {
                        Label("QR-kód beolvasása", systemImage: "qrcode.viewfinder")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 6)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)

                    VStack(alignment: .leading, spacing: 10) {
                        Text("Vagy kézzel").font(.headline)
                        TextField("Cím, pl. 192.168.1.12:47820", text: $address)
                            .keyboardType(.numbersAndPunctuation)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .textFieldStyle(.roundedBorder)
                        TextField("6 jegyű kód", text: $code)
                            .keyboardType(.numberPad)
                            .textFieldStyle(.roundedBorder)
                        Button("Párosítás") {
                            guard let info = PairingInfo(address: address, code: code) else {
                                message = "Adj meg egy címet (pl. 192.168.1.12:47820) és a 6 jegyű kódot."
                                return
                            }
                            connect(info)
                        }
                        .buttonStyle(.bordered)
                        .disabled(busy)
                    }
                    .padding()
                    .background(Theme.card, in: RoundedRectangle(cornerRadius: 16))

                    if busy { ProgressView("Kapcsolódás…") }
                    if let message {
                        Text(message).font(.footnote).foregroundStyle(Theme.amber).multilineTextAlignment(.center)
                    }
                }
                .padding()
            }
            .background(Theme.background.ignoresSafeArea())
            .sheet(isPresented: $scanning) {
                QRScannerSheet { text in
                    scanning = false
                    if let info = PairingInfo(url: text) {
                        connect(info)
                    } else {
                        message = "Ez nem DOGSAPKA párosító QR-kód."
                    }
                }
            }
        }
    }

    private func connect(_ info: PairingInfo) {
        busy = true
        message = nil
        Task {
            do {
                try await store.pair(info)
            } catch {
                message = error.localizedDescription
            }
            busy = false
        }
    }
}

// MARK: QR scanner

struct QRScannerSheet: View {
    var onCode: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var denied = false

    var body: some View {
        NavigationStack {
            ZStack {
                if denied {
                    VStack(spacing: 12) {
                        Image(systemName: "camera.fill").font(.largeTitle)
                        Text("A kamerához nincs hozzáférés. Engedélyezd: Beállítások → DOGSAPKA → Kamera.")
                            .multilineTextAlignment(.center)
                    }
                    .padding()
                } else {
                    QRScannerView(onCode: onCode)
                        .ignoresSafeArea()
                    RoundedRectangle(cornerRadius: 24)
                        .stroke(Theme.cyan, lineWidth: 3)
                        .frame(width: 250, height: 250)
                }
            }
            .navigationTitle("QR-kód beolvasása")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Mégse") { dismiss() } } }
            .task {
                switch AVCaptureDevice.authorizationStatus(for: .video) {
                case .authorized: break
                case .notDetermined: denied = !(await AVCaptureDevice.requestAccess(for: .video))
                default: denied = true
                }
            }
        }
    }
}

struct QRScannerView: UIViewControllerRepresentable {
    var onCode: (String) -> Void

    func makeUIViewController(context: Context) -> ScannerController {
        let vc = ScannerController()
        vc.onCode = onCode
        return vc
    }

    func updateUIViewController(_ vc: ScannerController, context: Context) {}
}

final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var onCode: ((String) -> Void)?
    private let session = AVCaptureSession()
    private var preview: AVCaptureVideoPreviewLayer?
    private var done = false

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        guard let device = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else { return }
        session.addInput(input)
        let output = AVCaptureMetadataOutput()
        guard session.canAddOutput(output) else { return }
        session.addOutput(output)
        output.setMetadataObjectsDelegate(self, queue: .main)
        output.metadataObjectTypes = [.qr]
        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        view.layer.addSublayer(layer)
        preview = layer
        DispatchQueue.global(qos: .userInitiated).async { [session] in session.startRunning() }
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        preview?.frame = view.bounds
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        if session.isRunning { session.stopRunning() }
    }

    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput objects: [AVMetadataObject],
                        from connection: AVCaptureConnection) {
        guard !done, let obj = objects.first as? AVMetadataMachineReadableCodeObject,
              let text = obj.stringValue else { return }
        done = true
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        onCode?(text)
    }
}
