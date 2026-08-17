// วาด icon ของ Labctl — squircle ไล่เฉด teal + monogram "lc" + cursor เรืองแสง
import AppKit

let size = 1024
let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
                           bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
                           isPlanar: false, colorSpaceName: .deviceRGB,
                           bytesPerRow: 0, bitsPerPixel: 0)!
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

func C(_ hex: UInt32, _ a: CGFloat = 1) -> NSColor {
    NSColor(calibratedRed: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: a)
}

// ---- พื้น squircle (สัดส่วนตาม macOS icon grid: เนื้อหา ~824px กึ่งกลาง) ----
let inset: CGFloat = 100
let boxRect = NSRect(x: inset, y: inset, width: 1024 - inset * 2, height: 1024 - inset * 2)
let box = NSBezierPath(roundedRect: boxRect, xRadius: 185, yRadius: 185)

// เงาใต้ icon เล็กน้อยให้ลอยจากพื้น (แบบ Big Sur)
let shadow = NSShadow()
shadow.shadowColor = NSColor.black.withAlphaComponent(0.35)
shadow.shadowOffset = NSSize(width: 0, height: -14)
shadow.shadowBlurRadius = 36
NSGraphicsContext.current?.saveGraphicsState()
shadow.set()
C(0x063C44).setFill()
box.fill()
NSGraphicsContext.current?.restoreGraphicsState()

// gradient 165° เหมือนแผง login: #0A6B78 → #08505A → #063C44
let grad = NSGradient(colorsAndLocations:
    (C(0x0A6B78), 0.0), (C(0x08505A), 0.62), (C(0x063C44), 1.0))!
grad.draw(in: box, angle: -75)  // บนซ้ายสว่าง → ล่างขวาเข้ม

// แสงนวลมุมบนซ้าย
let glow = NSGradient(colorsAndLocations:
    (NSColor.white.withAlphaComponent(0.13), 0.0),
    (NSColor.white.withAlphaComponent(0.0), 1.0))!
NSGraphicsContext.current?.saveGraphicsState()
box.addClip()
glow.draw(fromCenter: NSPoint(x: 330, y: 760), radius: 0,
          toCenter: NSPoint(x: 330, y: 760), radius: 620, options: [])
// เส้นขอบในบาง ๆ ให้ขอบคม
NSColor.white.withAlphaComponent(0.09).setStroke()
let innerBox = NSBezierPath(roundedRect: boxRect.insetBy(dx: 3, dy: 3), xRadius: 182, yRadius: 182)
innerBox.lineWidth = 6
innerBox.stroke()
NSGraphicsContext.current?.restoreGraphicsState()

// ---- monogram "lc" + cursor ----
let font = NSFont(name: "Menlo-Bold", size: 400) ?? NSFont.monospacedSystemFont(ofSize: 400, weight: .bold)
let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.white]
let text = NSAttributedString(string: "lc", attributes: attrs)
let tsize = text.size()

let cursorW: CGFloat = 74, cursorGap: CGFloat = 46
let totalW = tsize.width + cursorGap + cursorW
let tx = (1024 - totalW) / 2
let ty = (1024 - tsize.height) / 2 - 10

// เงาตัวอักษรจาง ๆ
let tShadow = NSShadow()
tShadow.shadowColor = NSColor.black.withAlphaComponent(0.25)
tShadow.shadowOffset = NSSize(width: 0, height: -8)
tShadow.shadowBlurRadius = 18
NSGraphicsContext.current?.saveGraphicsState()
tShadow.set()
text.draw(at: NSPoint(x: tx, y: ty))
NSGraphicsContext.current?.restoreGraphicsState()

// cursor block สี #7EE3E9 เรืองแสง — ตัวเดียวกับใน job log
let baseline = ty - font.descender  // จุด baseline จริงของข้อความ
// cursor สูงเท่า x-height (เท่าตัว c) เหมือน block cursor จริงใน terminal
let cursorH = font.xHeight + 14
let cursorRect = NSRect(x: tx + tsize.width + cursorGap,
                        y: baseline - 7, width: cursorW, height: cursorH)
let cursorGlow = NSShadow()
cursorGlow.shadowColor = C(0x7EE3E9, 0.85)
cursorGlow.shadowOffset = .zero
cursorGlow.shadowBlurRadius = 60
NSGraphicsContext.current?.saveGraphicsState()
cursorGlow.set()
C(0x7EE3E9).setFill()
NSBezierPath(roundedRect: cursorRect, xRadius: 18, yRadius: 18).fill()
NSGraphicsContext.current?.restoreGraphicsState()

NSGraphicsContext.restoreGraphicsState()

let png = rep.representation(using: .png, properties: [:])!
let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon_1024.png"
try! png.write(to: URL(fileURLWithPath: out))
print("wrote \(out)")
