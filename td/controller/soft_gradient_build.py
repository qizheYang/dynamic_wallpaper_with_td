"""Build the soft_gradient v1 template network inside TouchDesigner from source.

The template is defined by this file plus shaders/soft_gradient.frag; the
saved td/wallpaper_template.toe is a build output (`python3 td/run_td.py build`).
The shader text is embedded in the pixel DAT, not synced to the file, so the
saved project alone determines what renders.

    /project1/soft_gradient      Base COMP, parent shortcut SoftGradient
        custom page Wallpaper: Frame, Framecount, Seed, Motioncycles, Debugchart, Debugindex
        glsl_art                 GLSL Multi TOP, 1280x720 rgba8fixed, uniforms <- custom pars
        glsl_art_pixel           docked pixel shader DAT (embedded shader text)
        glsl_art_info            docked info DAT (compile log)
        null_out                 Null TOP: the image the export controller saves
"""
import pathlib

SHADER = "td/shaders/soft_gradient.frag"
WIDTH, HEIGHT = 1280, 720
PIXEL_FORMAT = "rgba8fixed"
# uniform name -> custom parameter driving it
UNIFORMS = (
    ("uFrame", "Frame"),
    ("uFrameCount", "Framecount"),
    ("uSeed", "Seed"),
    ("uCycles", "Motioncycles"),
    ("uDebugChart", "Debugchart"),
    ("uDebugIndex", "Debugindex"),
)


def _par(group):
    """appendX returns a ParGroup in TD 2025; the single-value Par is its first item."""
    try:
        return group[0]
    except (TypeError, IndexError):
        return group


def build(parent, shader_path):
    import td
    shader = pathlib.Path(shader_path).read_text(encoding="utf-8")
    comp = parent.create(td.baseCOMP, "soft_gradient")
    comp.par.parentshortcut = "SoftGradient"
    comp.nodeX, comp.nodeY = 0, 0
    comp.viewer = True
    comp.comment = "soft_gradient v1 wallpaper template; built by td/controller/soft_gradient_build.py"

    page = comp.appendCustomPage("Wallpaper")
    frame = _par(page.appendInt("Frame", label="Frame"))
    frame.min, frame.clampMin, frame.normMin, frame.normMax = 0, True, 0, 479
    count = _par(page.appendInt("Framecount", label="Frame Count"))
    count.min, count.clampMin, count.normMin, count.normMax = 2, True, 2, 960
    count.default = count.val = 480
    seed = _par(page.appendInt("Seed", label="Seed"))
    seed.normMin, seed.normMax = 0, 1000
    cycles = _par(page.appendInt("Motioncycles", label="Motion Cycles"))
    cycles.min, cycles.max, cycles.clampMin, cycles.clampMax = 1, 8, True, True
    cycles.normMin, cycles.normMax = 1, 8
    cycles.default = cycles.val = 1
    _par(page.appendToggle("Debugchart", label="Debug Chart"))
    _par(page.appendToggle("Debugindex", label="Debug Index"))

    glsl = comp.create(td.glslmultiTOP, "glsl_art")
    glsl.nodeX, glsl.nodeY = 0, 0
    glsl.viewer = True
    compute = comp.op("glsl_art_compute")
    if compute is not None:  # pixel shader only; the compute dock is unused
        compute.destroy()
    pixel = comp.op("glsl_art_pixel")
    pixel.text = shader
    pixel.par.language = "glsl"
    glsl.par.pixeldat = pixel.name
    glsl.par.outputresolution = "custom"
    glsl.par.resolutionw, glsl.par.resolutionh = WIDTH, HEIGHT
    glsl.par.resmult = False
    glsl.par.format = PIXEL_FORMAT
    glsl.seq.vec.numBlocks = len(UNIFORMS)
    for index, (uniform, source) in enumerate(UNIFORMS):
        glsl.par[f"vec{index}name"] = uniform
        glsl.par[f"vec{index}valuex"].expr = f"parent.SoftGradient.par.{source}"

    null = comp.create(td.nullTOP, "null_out")
    null.nodeX, null.nodeY = 250, 0
    null.viewer = True
    null.inputConnectors[0].connect(glsl)
    return comp


def check(comp):
    """Inspect a built template after it has cooked; returns facts and problems."""
    glsl, null, info_dat = comp.op("glsl_art"), comp.op("null_out"), comp.op("glsl_art_info")
    problems = []
    for target in (comp, glsl, null):
        for kind, text in (("error", target.errors(recurse=False)), ("warning", target.warnings(recurse=False))):
            if text:
                problems.append(f"{target.path} {kind}: {text}")
    compile_log = info_dat.text if info_dat is not None else None
    if (null.width, null.height) != (WIDTH, HEIGHT):
        problems.append(f"null_out is {null.width}x{null.height}, expected {WIDTH}x{HEIGHT}")
    if glsl.par.format.eval() != PIXEL_FORMAT:
        problems.append(f"glsl_art format is {glsl.par.format.eval()}")
    uniforms = {}
    for index, (uniform, source) in enumerate(UNIFORMS):
        name = glsl.par[f"vec{index}name"].eval()
        if name != uniform:
            problems.append(f"vec{index}name is {name!r}, expected {uniform!r}")
        uniforms[uniform] = glsl.par[f"vec{index}valuex"].eval()
    color = {}
    for target in (glsl, null):
        for par in target.pars("parmcolorspace", "parmreferencewhite"):
            color[f"{target.name}.{par.name}"] = str(par.eval())
    return {
        "problems": problems,
        "compile_log": (compile_log or "")[:4000],
        "output_size": [null.width, null.height],
        "uniform_values": uniforms,
        "color_parameters": color,
        "children": sorted(child.name for child in comp.children),
    }
