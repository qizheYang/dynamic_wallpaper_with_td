// soft_gradient v1: slow, stateless, periodic abstract gradient (GLSL TOP pixel shader).
//
// Frame i of N renders phase p = i/N. Every time-dependent term is a sin/cos of
// an integer multiple of t = 2*pi*cycles*p, so frame N would equal frame 0 and
// the loop closes without rendering a duplicate endpoint.
//
// Colors are mixed in linear light and encoded with the BT.709 OETF, so the
// written values match the pipeline contract `rgb_rec709_bt709trc_full`,
// PROVIDED TouchDesigner stores these 8-bit values unchanged in the PNG. That
// assumption is checked with uDebugChart before any real export.
//
// Status: written outside TouchDesigner; not yet compiled or run in TD.

uniform float uFrame;       // i, 0..N-1
uniform float uFrameCount;  // N
uniform float uSeed;        // job seed
uniform float uCycles;      // integer motion cycles per loop (>= 1)
uniform float uDebugChart;  // 1: output the known-value chart instead of the art
uniform float uDebugIndex;  // 1: overlay the binary frame-index strip

out vec4 fragColor;

const float TAU = 6.28318530718;
const float INDEX_BITS = 12.0;        // must match pipeline/verify.py
const float INDEX_STRIP = 1.0 / 24.0; // bottom fraction of the height

float hash11(float x) { return fract(sin(x * 127.1 + 311.7) * 43758.5453); }

vec3 oetf709(vec3 L) {
    L = clamp(L, 0.0, 1.0);
    vec3 lo = 4.5 * L;
    vec3 hi = 1.099 * pow(L, vec3(0.45)) - 0.099;
    return mix(lo, hi, step(vec3(0.018), L));
}

// Known code values, left to right; must match pipeline/verify.py CHART.
vec3 chart(float x) {
    int bar = int(clamp(floor(x * 8.0), 0.0, 7.0));
    if (bar == 0) return vec3(0.0);
    if (bar == 1) return vec3(0.25);
    if (bar == 2) return vec3(0.5);
    if (bar == 3) return vec3(0.75);
    if (bar == 4) return vec3(1.0);
    if (bar == 5) return vec3(1.0, 0.0, 0.0);
    if (bar == 6) return vec3(0.0, 1.0, 0.0);
    return vec3(0.0, 0.0, 1.0);
}

vec3 art(vec2 uv, float t) {
    float aspect = uTDOutputInfo.res.z / uTDOutputInfo.res.w;
    vec2 p = vec2((uv.x - 0.5) * aspect, uv.y - 0.5);
    // Dusk background: deep blue at the bottom to violet at the top (linear light).
    vec3 col = mix(vec3(0.004, 0.006, 0.030), vec3(0.060, 0.022, 0.110), smoothstep(0.0, 1.0, uv.y));
    vec3 tint[3] = vec3[3](vec3(0.55, 0.16, 0.05), vec3(0.32, 0.05, 0.22), vec3(0.06, 0.10, 0.36));
    for (int k = 0; k < 3; k++) {
        float fk = float(k);
        float a = TAU * hash11(uSeed + fk * 17.0);
        float dir = (k == 1) ? -1.0 : 1.0;           // one blob orbits the other way
        vec2 center = vec2(0.28 * cos(dir * t + a), 0.16 * sin(dir * t + a))
                    + vec2(0.25 * (fk - 1.0), -0.05 * fk);
        float radius = 0.34 + 0.05 * sin(2.0 * t + a);  // integer multiple of t: periodic
        vec2 d = p - center;
        col += tint[k] * exp(-dot(d, d) / (radius * radius));
    }
    return col;
}

void main() {
    vec2 uv = vUV.st;
    float t = TAU * uCycles * (uFrame / uFrameCount);
    vec3 value = (uDebugChart > 0.5) ? chart(uv.x) : oetf709(art(uv, t));
    if (uDebugIndex > 0.5 && uv.y < INDEX_STRIP) {
        float cell = clamp(floor(uv.x * INDEX_BITS), 0.0, INDEX_BITS - 1.0);  // MSB first
        float bit = mod(floor((uFrame + 0.5) / exp2(INDEX_BITS - 1.0 - cell)), 2.0);
        value = vec3(bit);
    }
    fragColor = TDOutputSwizzle(vec4(value, 1.0));
}
