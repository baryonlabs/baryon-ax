#version 460 core
// 바리온 비서's face: the brand's "B" turned on its side becomes a face. The B's spine is the brow,
// its two bowls are the eyes, and a mouth sits below. Drawn on a glossy navy sphere so it reads as a
// small 3D head that turns, blinks, listens (ripples) and talks (mouth follows the voice).
// Uniform order matters: lib/face.dart sets them by index.
#include <flutter/runtime_effect.glsl>

uniform vec2 uSize;
uniform float uTime;
uniform float uMouth;   // 0..1 how far the mouth is open (speech level)
uniform float uListen;  // 0..1 how loud the caller is (ripples)
uniform float uEyeOpen; // 0 = the B's bowls as calm arcs, 1 = open eyes with pupils
uniform float uSmile;   // -1 frown .. 1 smile
uniform float uBrow;    // -1 worried .. 1 one brow raised (a question)
uniform float uThink;   // 0..1 dots circling, eyes up
uniform float uBlink;   // 0..1
uniform vec2 uLook;     // head turn, -1..1
uniform float uGlow;    // 0..1 rim light while speaking
uniform float uBob;     // -1..1 floating

out vec4 fragColor;

const vec3 NAVY = vec3(0.075, 0.125, 0.235);
const vec3 NAVY_LIGHT = vec3(0.16, 0.26, 0.45);
const vec3 CREAM = vec3(0.97, 0.94, 0.86);
const vec3 TEAL = vec3(0.36, 0.78, 0.86);
const float R = 0.66;

// Soft edge: 1 inside a stroke of half-width w around distance d.
float stroke(float d, float w, float aa) {
    return 1.0 - smoothstep(w - aa, w + aa, d);
}

float fill(float d, float aa) {
    return 1.0 - smoothstep(-aa, aa, d);
}

void main() {
    vec2 frag = FlutterFragCoord().xy;
    float scale = 0.5 * min(uSize.x, uSize.y);
    vec2 p = (frag - 0.5 * uSize) / scale;
    p.y = -p.y;
    float aa = 1.6 / scale;

    vec2 center = vec2(0.0, 0.06 + 0.05 * uBob);
    vec2 q = (p - center) / R;
    float r = length(q);

    vec4 color = vec4(0.0);

    // Shadow on the "floor": smaller and fainter when the head floats higher.
    vec2 s = (p - vec2(0.0, -0.86)) / vec2(0.46 - 0.05 * uBob, 0.075);
    float shadow = (1.0 - smoothstep(0.2, 1.0, length(s))) * (0.22 - 0.06 * uBob);
    color = vec4(vec3(0.0), shadow);

    // Listening: ripples leave the head, stronger when the caller is louder.
    if (r > 1.0) {
        float waves = 0.0;
        for (int i = 0; i < 3; i++) {
            float phase = fract(uTime * 0.55 + float(i) / 3.0);
            float radius = 1.0 + phase * 0.42;
            waves += (1.0 - phase) * stroke(abs(r - radius), 0.018, aa / R);
        }
        float a = clamp(waves * (0.25 + 0.75 * uListen) * smoothstep(0.0, 0.08, uListen + 0.02) * step(0.001, uListen), 0.0, 1.0);
        color = mix(color, vec4(TEAL, 1.0), a * 0.9);

        // Thinking: three dots go round, one after another (동… 동… 동…).
        for (int i = 0; i < 3; i++) {
            float angle = uTime * 1.7 + float(i) * 2.0944;
            float bounce = 0.5 + 0.5 * sin(uTime * 4.2 - float(i) * 1.4);
            vec2 orb = center + R * (1.16 + 0.04 * bounce) * vec2(cos(angle), sin(angle));
            float d = length(p - orb) - (0.03 + 0.012 * bounce);
            color = mix(color, vec4(CREAM, 1.0), fill(d, aa) * uThink);
        }
    }

    if (r <= 1.0 + aa / R) {
        float z = sqrt(max(0.0, 1.0 - r * r));
        vec3 n = vec3(q, z);

        // Turn the head: features live on the rotated sphere, so they curve with it.
        float yaw = 0.45 * uLook.x;
        float pitch = 0.35 * uLook.y;
        vec3 f = n;
        f = vec3(f.x * cos(yaw) - f.z * sin(yaw), f.y, f.x * sin(yaw) + f.z * cos(yaw));
        f = vec3(f.x, f.y * cos(pitch) - f.z * sin(pitch), f.y * sin(pitch) + f.z * cos(pitch));

        // Glass-like navy: diffuse light from the upper left, a sharp highlight, a rim of light.
        vec3 light = normalize(vec3(-0.62, 0.78, 0.5));
        float diffuse = max(dot(n, light), 0.0);
        float spec = pow(max(dot(reflect(-light, n), vec3(0.0, 0.0, 1.0)), 0.0), 60.0);
        float rim = pow(1.0 - z, 2.6);
        vec3 base = mix(NAVY, NAVY_LIGHT, clamp(0.5 + 0.5 * (q.y - q.x * 0.4), 0.0, 1.0));
        vec3 col = base * (0.42 + 0.8 * diffuse);
        col += rim * mix(vec3(0.25, 0.35, 0.6), TEAL, uGlow) * (0.55 + 0.9 * uGlow);

        // The face, in the rotated sphere's front plane (about -1..1).
        vec2 fp = f.xy / 1.18;  // features a little larger than the sphere's own scale
        float facing = smoothstep(0.05, 0.4, f.z);
        float ink = 0.0;   // cream strokes
        float hole = 0.0;  // the open mouth's inside

        // Brow = the B's spine, tilted by a raised brow (question) or knitted (worry).
        float browY = 0.2 + uBrow * 0.07 * smoothstep(-0.45, 0.45, fp.x) - min(uBrow, 0.0) * 0.05 * (1.0 - abs(fp.x) / 0.45);
        float browHalf = 0.44;
        float onBrow = stroke(abs(fp.y - browY), 0.034, aa) * (1.0 - smoothstep(browHalf - aa, browHalf + aa, abs(fp.x)));
        ink = max(ink, onBrow);

        // Eyes = the B's bowls, hanging under the spine. Blinking flattens them.
        float open = clamp(uEyeOpen * (1.0 - uBlink), 0.0, 1.0);
        for (int side = 0; side < 2; side++) {
            float sx = side == 0 ? -1.0 : 1.0;
            vec2 c = vec2(sx * 0.215, browY);
            float squash = mix(1.0, 0.22, uBlink);
            vec2 e = (fp - c) / vec2(1.0, squash);
            float bowl = stroke(abs(length(e) - 0.2), 0.034, aa) * step(fp.y, c.y);
            ink = max(ink, bowl);
            // Open eyes: a pupil inside the bowl that looks where the head turns (or up, thinking).
            vec2 look = vec2(0.045 * uLook.x + 0.04 * uThink, -0.1 + 0.05 * uThink + 0.02 * uLook.y);
            float pupil = fill(length((fp - c - look) / vec2(1.0, squash)) - 0.062 * open, aa);
            ink = max(ink, pupil * step(0.02, open));
        }

        // Mouth: a curve that smiles or frowns, opening to an oval when speaking.
        float mw = 0.2 + 0.03 * max(uSmile, 0.0);
        float mx = fp.x / mw;
        float curve = uSmile * 0.11 * (1.0 - mx * mx);
        float my = -0.33 - curve;
        float h = 0.012 + 0.15 * uMouth;
        float ellipse = length(vec2(mx, (fp.y - my) / h));
        float inside = 1.0 - smoothstep(1.0 - aa / h, 1.0 + aa / h, ellipse);
        float lips = stroke(abs(ellipse - 1.0) * min(h, mw), 0.026, aa) * step(abs(mx), 1.05);
        ink = max(ink, lips);
        hole = inside * smoothstep(0.03, 0.08, uMouth);

        col = mix(col, vec3(0.035, 0.03, 0.06), hole * facing);
        col = mix(col, CREAM, ink * facing);

        // Cheeks warm up when smiling.
        for (int side = 0; side < 2; side++) {
            float sx = side == 0 ? -1.0 : 1.0;
            float cheek = 1.0 - smoothstep(0.0, 0.14, length(fp - vec2(sx * 0.42, -0.2)));
            col = mix(col, vec3(0.95, 0.55, 0.55), cheek * 0.22 * max(uSmile, 0.0) * facing);
        }

        col += spec * 0.75;
        float edge = 1.0 - smoothstep(1.0 - aa / R, 1.0 + aa / R, r);
        color = mix(color, vec4(col, 1.0), edge);
    }

    fragColor = vec4(color.rgb * color.a, color.a);
}
