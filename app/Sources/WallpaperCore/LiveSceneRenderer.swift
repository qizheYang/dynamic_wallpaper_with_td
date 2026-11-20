import AppKit
import QuartzCore

public struct SceneColor: Codable, Equatable, Sendable {
    public var red: Double
    public var green: Double
    public var blue: Double
    public init(red: Double, green: Double, blue: Double) {
        self.red = red; self.green = green; self.blue = blue
    }
    public init?(hex: String) {
        guard hex.count == 7, hex.first == "#", hex.dropFirst().allSatisfy({ $0.isHexDigit }),
              let value = UInt32(hex.dropFirst(), radix: 16) else { return nil }
        self.init(red: Double((value >> 16) & 255) / 255, green: Double((value >> 8) & 255) / 255,
                  blue: Double(value & 255) / 255)
    }
    fileprivate var color: NSColor {
        NSColor(srgbRed: bounded(red), green: bounded(green), blue: bounded(blue), alpha: 1)
    }
}

/// Constrained native scene properties, not an interpreter for arbitrary TD/Workshop content.
public struct NativeEffectState: Codable, Equatable, Sendable {
    public var daylight: Double
    public var cloud: Double
    public var wind: Double
    public var intensity: Double
    public var snow: Bool
    public var accent: SceneColor?
    public init(daylight: Double = 0.5, cloud: Double = 0.3, wind: Double = 0.2,
                intensity: Double = 0, snow: Bool = false, accent: SceneColor? = nil) {
        self.daylight = daylight; self.cloud = cloud; self.wind = wind
        self.intensity = intensity; self.snow = snow; self.accent = accent
    }

    public init(weather: WeatherSnapshot) {
        self.init(daylight: weather.isDay ? 1 : 0,
                  cloud: weather.kind == .fog ? max(0.8, weather.cloudFraction) : weather.cloudFraction,
                  wind: min(1, weather.windMetresPerSecond / 20),
                  intensity: [.rain, .snow, .storm].contains(weather.kind)
                    ? max(0.2, min(1, weather.precipitationMillimetres / 5)) : 0,
                  snow: weather.kind == .snow)
    }
}

private func bounded(_ value: Double) -> Double { value.isFinite ? min(1, max(0, value)) : 0 }

/// A small layer tree: gradient + three clouds + eighteen precipitation strokes.
/// Animations request 24 fps; CoreAnimation's preferred range is an OS hint, not a measured hard cap.
/// No display link, polling timer, network work, model or TouchDesigner runtime is involved.
@MainActor public final class NativeSceneView: NSView {
    private let sky = CAGradientLayer()
    private var clouds: [CAShapeLayer] = []
    private var drops: [CAShapeLayer] = []
    private var hasData = false
    public private(set) var effect = NativeEffectState()
    public private(set) var isRunning = false

    public override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer = CALayer(); layer?.masksToBounds = true
        sky.startPoint = CGPoint(x: 0.2, y: 1); sky.endPoint = CGPoint(x: 0.8, y: 0)
        layer?.addSublayer(sky)
        for _ in 0..<3 {
            let cloud = CAShapeLayer(); clouds.append(cloud); layer?.addSublayer(cloud)
        }
        for _ in 0..<18 {
            let drop = CAShapeLayer(); drops.append(drop); layer?.addSublayer(drop)
        }
        rebuild()
    }
    public required init?(coder: NSCoder) { nil }

    public func apply(snapshot: WeatherSnapshot?) {
        hasData = snapshot != nil
        effect = snapshot.map(NativeEffectState.init(weather:)) ?? NativeEffectState()
        rebuild()
    }
    public func apply(effect: NativeEffectState) {
        hasData = true
        self.effect = effect
        rebuild()
    }

    public func setRunning(_ running: Bool) {
        guard isRunning != running else { return }
        isRunning = running
        rebuild()
    }

    public override func layout() {
        super.layout()
        rebuild()
    }

    private func rebuild() {
        guard layer != nil else { return }
        CATransaction.begin(); CATransaction.setDisableActions(true)
        defer { CATransaction.commit() }
        sky.removeAllAnimations(); clouds.forEach { $0.removeAllAnimations() }; drops.forEach { $0.removeAllAnimations() }
        let day = bounded(effect.daylight), cloud = bounded(effect.cloud)
        let wind = bounded(effect.wind), intensity = bounded(effect.intensity)
        let top = effect.accent?.color ?? NSColor(srgbRed: 0.035 + day * 0.14,
                                                 green: 0.055 + day * 0.27,
                                                 blue: 0.12 + day * 0.4, alpha: 1)
        let bottom = NSColor(srgbRed: 0.15 + day * 0.39, green: 0.12 + day * 0.44,
                             blue: 0.23 + day * 0.39, alpha: 1)
        sky.frame = bounds; sky.colors = [bottom.cgColor, top.cgColor]
        let width = bounds.width, height = bounds.height
        for (index, shape) in clouds.enumerated() {
            let scale = CGFloat(0.2 + Double(index) * 0.04)
            let size = CGSize(width: width * scale, height: height * 0.11)
            shape.frame = CGRect(x: width * CGFloat(0.08 + Double(index) * 0.3),
                                 y: height * CGFloat(0.65 + Double(index % 2) * 0.14),
                                 width: size.width, height: size.height)
            let path = CGMutablePath()
            path.addEllipse(in: CGRect(x: 0, y: 0, width: size.width, height: size.height * 0.65))
            path.addEllipse(in: CGRect(x: size.width * 0.23, y: size.height * 0.15,
                                      width: size.width * 0.48, height: size.height * 0.85))
            shape.path = path
            shape.fillColor = NSColor(white: 0.55 + day * 0.3, alpha: 0.05 + cloud * 0.25).cgColor
            shape.isHidden = cloud < 0.05
            if isRunning && hasData && !shape.isHidden && width > 0 {
                let drift = CABasicAnimation(keyPath: "transform.translation.x")
                drift.fromValue = -width * 0.025; drift.toValue = width * 0.025
                drift.duration = 24 - 14 * wind; drift.autoreverses = true
                repeatAnimation(drift, on: shape, key: "cloud")
            }
        }
        for (index, shape) in drops.enumerated() {
            let x = width * CGFloat((Double(index) * 0.61803398875).truncatingRemainder(dividingBy: 1))
            let y = height * CGFloat((Double(index) * 0.41421356237).truncatingRemainder(dividingBy: 1))
            shape.frame = CGRect(x: x, y: y, width: 10, height: 24)
            let path = CGMutablePath()
            if effect.snow { path.addEllipse(in: CGRect(x: 0, y: 0, width: 3, height: 3)) }
            else { path.move(to: .zero); path.addLine(to: CGPoint(x: -4 - 5 * wind, y: 17)) }
            shape.path = path; shape.lineWidth = 1
            shape.fillColor = effect.snow ? NSColor(white: 0.9, alpha: 0.55).cgColor : nil
            shape.strokeColor = NSColor(white: 0.8, alpha: 0.35).cgColor
            shape.isHidden = intensity <= 0 || Double(index) >= 4 + intensity * 14
            if isRunning && hasData && !shape.isHidden && height > 0 {
                let fall = CABasicAnimation(keyPath: "transform.translation.y")
                fall.fromValue = height; fall.toValue = -height
                fall.duration = effect.snow ? 12 : 4.5
                fall.beginTime = CACurrentMediaTime() - Double(index) * 0.37
                repeatAnimation(fall, on: shape, key: "precipitation")
            }
        }
    }

    private func repeatAnimation(_ animation: CABasicAnimation, on layer: CALayer, key: String) {
        animation.repeatCount = .infinity
        animation.preferredFrameRateRange = CAFrameRateRange(minimum: 12, maximum: 24, preferred: 24)
        layer.add(animation, forKey: key)
    }
}
