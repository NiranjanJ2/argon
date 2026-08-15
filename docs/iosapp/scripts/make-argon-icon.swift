// Renders the Argon mark: a neon Erlenmeyer flask, glowing cyan, on transparency.
// CoreGraphics rather than an image library because none is installed here, and
// this needs to be a real 1024pt asset rather than something upscaled.

import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

let size = 1024.0
let cyan = CGColor(red: 0.16, green: 0.83, blue: 1.00, alpha: 1.0)      // outline
let liquid = CGColor(red: 0.11, green: 0.63, blue: 1.00, alpha: 1.0)    // fill
let glow = CGColor(red: 0.20, green: 0.80, blue: 1.00, alpha: 0.95)

guard
  let space = CGColorSpace(name: CGColorSpace.sRGB),
  let ctx = CGContext(
    data: nil, width: Int(size), height: Int(size), bitsPerComponent: 8,
    bytesPerRow: 0, space: space,
    bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
else { fatalError("could not create context") }

ctx.setAllowsAntialiasing(true)
ctx.interpolationQuality = .high

// Geometry, in a 1024 box. Neck is narrow and vertical; the body flares to a
// wide base with softly rounded bottom corners, which is what reads as
// "Erlenmeyer" rather than "cup" at small sizes.
let cx = size / 2
let rimY = 246.0, rimHalf = 116.0, rimRy = 30.0
let neckBottomY = 486.0, neckHalf = 86.0
let baseY = 900.0, baseHalf = 330.0
let corner = 54.0
let lineWidth = 26.0

func flaskBody() -> CGPath {
  let p = CGMutablePath()
  p.move(to: CGPoint(x: cx - neckHalf, y: rimY))
  p.addLine(to: CGPoint(x: cx - neckHalf, y: neckBottomY))
  p.addLine(to: CGPoint(x: cx - baseHalf + corner * 0.45, y: baseY - corner))
  p.addQuadCurve(
    to: CGPoint(x: cx - baseHalf + corner, y: baseY),
    control: CGPoint(x: cx - baseHalf, y: baseY))
  p.addLine(to: CGPoint(x: cx + baseHalf - corner, y: baseY))
  p.addQuadCurve(
    to: CGPoint(x: cx + baseHalf - corner * 0.45, y: baseY - corner),
    control: CGPoint(x: cx + baseHalf, y: baseY))
  p.addLine(to: CGPoint(x: cx + neckHalf, y: neckBottomY))
  p.addLine(to: CGPoint(x: cx + neckHalf, y: rimY))
  return p
}

// Liquid sits in the lower body, clipped to the flask so it cannot spill past
// the glass edge.
func liquidPath(level: Double) -> CGPath {
  let p = CGMutablePath()
  let t = (level - neckBottomY) / (baseY - neckBottomY)
  let halfAt = neckHalf + (baseHalf - neckHalf) * t
  p.move(to: CGPoint(x: cx - halfAt, y: level))
  p.addLine(to: CGPoint(x: cx - baseHalf + corner * 0.45, y: baseY - corner))
  p.addQuadCurve(
    to: CGPoint(x: cx - baseHalf + corner, y: baseY),
    control: CGPoint(x: cx - baseHalf, y: baseY))
  p.addLine(to: CGPoint(x: cx + baseHalf - corner, y: baseY))
  p.addQuadCurve(
    to: CGPoint(x: cx + baseHalf - corner * 0.45, y: baseY - corner),
    control: CGPoint(x: cx + baseHalf, y: baseY))
  p.addLine(to: CGPoint(x: cx + halfAt, y: level))
  p.closeSubpath()
  return p
}

// CoreGraphics origin is bottom-left; the numbers above read top-down.
ctx.translateBy(x: 0, y: size)
ctx.scaleBy(x: 1, y: -1)

let body = flaskBody()
let rim = CGPath(
  ellipseIn: CGRect(x: cx - rimHalf, y: rimY - rimRy, width: rimHalf * 2, height: rimRy * 2),
  transform: nil)

// Liquid first, under the glass.
ctx.saveGState()
ctx.addPath(body)
ctx.clip()
ctx.setShadow(offset: .zero, blur: 46, color: glow)
ctx.addPath(liquidPath(level: 690))
ctx.setFillColor(liquid)
ctx.fillPath()
ctx.restoreGState()

// Two passes of the outline: a wide soft one for the halo, then the crisp edge.
ctx.setLineWidth(lineWidth)
ctx.setLineCap(.round)
ctx.setLineJoin(.round)
ctx.setStrokeColor(cyan)

for blur in [58.0, 22.0, 0.0] {
  ctx.saveGState()
  if blur > 0 { ctx.setShadow(offset: .zero, blur: blur, color: glow) }
  ctx.addPath(body)
  ctx.strokePath()
  ctx.restoreGState()
}

// The rim gets one soft pass and one crisp one. Running it through the same
// three-pass halo as the body packed enough glow into a small ellipse to fill
// its middle, and the opening stopped reading as an opening.
for blur in [20.0, 0.0] {
  ctx.saveGState()
  if blur > 0 { ctx.setShadow(offset: .zero, blur: blur, color: glow) }
  ctx.addPath(rim)
  ctx.strokePath()
  ctx.restoreGState()
}

// Bubbles in the empty upper body.
for (bx, by, r) in [(cx - 34.0, 566.0, 17.0), (cx + 8.0, 618.0, 27.0)] {
  ctx.saveGState()
  ctx.setShadow(offset: .zero, blur: 26, color: glow)
  ctx.setLineWidth(13)
  ctx.addEllipse(in: CGRect(x: bx - r, y: by - r, width: r * 2, height: r * 2))
  ctx.strokePath()
  ctx.restoreGState()
}

guard let image = ctx.makeImage() else { fatalError("no image") }
let out = URL(fileURLWithPath: CommandLine.arguments[1])
guard
  let dest = CGImageDestinationCreateWithURL(out as CFURL, UTType.png.identifier as CFString, 1, nil)
else { fatalError("no destination") }
CGImageDestinationAddImage(dest, image, nil)
CGImageDestinationFinalize(dest)
print("wrote \(out.path)")
