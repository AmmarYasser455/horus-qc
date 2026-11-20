"""Catalogue of every Horus QC check: severity, guidance text and auto-fix."""

from dataclasses import dataclass, field

CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"

SEVERITY_RANK = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}
SEVERITY_COLORS = {
    CRITICAL: "#d7191c",
    HIGH: "#f07c1a",
    MEDIUM: "#f2c12e",
    LOW: "#2b9fb3",
}

BUILDING = "building"
ROAD = "road"


@dataclass(frozen=True)
class CheckDef:
    key: str
    target: str
    title: str
    severity: str
    why: str
    steps: tuple
    autofix: str = ""  # fix id understood by core.fixes, "" = manual only
    autofix_label: str = ""
    default_on: bool = True
    live: bool = False  # also run instantly after each edit
    extra: dict = field(default_factory=dict)


_CHECKS = [
    # ------------------------------------------------------------------ buildings
    CheckDef(
        "b_null", BUILDING, "Empty building", CRITICAL,
        "The feature has no geometry, so it can't be shown, measured or checked.",
        ("Open the attribute table and find the feature.",
         "Delete it, or digitise its footprint again."),
        "delete", "Delete the empty feature",
    ),
    CheckDef(
        "b_invalid", BUILDING, "Invalid geometry", HIGH,
        "GEOS can't use this polygon (for example a hole outside its shell or a ring with too few points). "
        "Areas, overlaps and exports involving it are unreliable.",
        ("Zoom to the marker; it sits on the exact problem location.",
         "Use the Vertex tool to move or delete the vertex causing the problem.",
         "Or run the auto-fix to repair it with makeValid."),
        "make_valid", "Repair with makeValid",
        live=True,
    ),
    CheckDef(
        "b_self_intersection", BUILDING, "Self-intersection (bow-tie)", HIGH,
        "The outline crosses itself, usually because two vertices were clicked in the wrong order.",
        ("Zoom to the marker at the crossing point.",
         "With the Vertex tool, drag one of the crossing vertices back to the correct corner.",
         "Or run the auto-fix: a 4-corner bow-tie is rebuilt as the rectangle you meant to draw; "
         "more complex outlines are repaired with makeValid."),
        "untangle", "Untangle the outline",
        live=True,
    ),
    CheckDef(
        "b_multipart", BUILDING, "Multipart building", MEDIUM,
        "One feature holds several separate polygons. Each building should be its own feature.",
        ("Check whether every part is a real building.",
         "Run the auto-fix to split the parts into separate features (attributes are copied)."),
        "split_parts", "Split into separate buildings",
        live=True,
    ),
    CheckDef(
        "b_duplicate", BUILDING, "Duplicate building", CRITICAL,
        "Two features cover almost the same footprint (95%+ of the larger one). "
        "This usually happens when a building is drawn twice or pasted twice.",
        ("Compare both outlines; the duplicate (newer feature id) is highlighted.",
         "Keep the better outline and delete the other one, or run the auto-fix to delete the duplicate."),
        "delete", "Delete the duplicate",
        live=True,
    ),
    CheckDef(
        "b_contained", BUILDING, "Building inside another", HIGH,
        "A smaller building lies completely inside a bigger one, which is almost always a digitising mistake.",
        ("Check the imagery: is it one building or two?",
         "If it's one, delete the inner feature (auto-fix).",
         "If they're two buildings, redraw the outer one so they share a wall instead."),
        "delete", "Delete the inner building",
        live=True,
    ),
    CheckDef(
        "b_overlap", BUILDING, "Overlapping buildings", HIGH,
        "Two buildings overlap by a large share of the smaller one. Usually one of them was shifted "
        "onto its neighbour or drawn on the roof instead of the base.",
        ("Look at the imagery and decide which outline is wrong.",
         "Shift that building back to its base (Review mode, keys G or 1-9).",
         "If they really are attached, run the auto-fix to cut the overlap out of the highlighted building."),
        "trim", "Cut the overlap from this building",
        live=True,
    ),
    CheckDef(
        "b_shared_wall", BUILDING, "Shared-wall overlap (sliver)", LOW,
        "Attached buildings overlap by a thin sliver along their common wall. This is common in "
        "attached rural houses and is usually a small digitising error.",
        ("Run the auto-fix to cut the sliver, which leaves a clean shared wall.",
         "Or use the Vertex tool with snapping on to align the wall vertices."),
        "trim", "Cut the sliver (clean shared wall)",
    ),
    CheckDef(
        "b_gap", BUILDING, "Hairline gap between buildings", LOW,
        "Two buildings almost touch but leave a tiny gap. On attached houses the walls should usually be shared.",
        ("Check the imagery: are the buildings attached?",
         "If so, run the auto-fix to snap this building's wall onto its neighbour.",
         "If there's a real alley, ignore this error."),
        "snap_gap", "Snap the wall onto the neighbour",
    ),
    CheckDef(
        "b_tiny", BUILDING, "Building too small", MEDIUM,
        "The footprint is smaller than the minimum building area. It's probably a stray click, a sliver left "
        "after an edit, or a shed that isn't in the specification.",
        ("Check the imagery.",
         "Delete it if it isn't a building, or redraw it at full size."),
        "delete", "Delete the tiny feature",
        live=True,
    ),
    CheckDef(
        "b_huge", BUILDING, "Building unusually large", MEDIUM,
        "The footprint is larger than the maximum expected area. It may be several buildings drawn as one, "
        "or a courtyard or field drawn by mistake.",
        ("Check the imagery.",
         "Split it into individual buildings with the Split Features tool if needed."),
    ),
    CheckDef(
        "b_not_square", BUILDING, "Corners almost square", LOW,
        "One or more corners are a few degrees off 90 deg. On rectangular buildings this is digitising "
        "wobble that makes the outline look sloppy.",
        ("Run the auto-fix to square the corners (QGIS Orthogonalize) while keeping the overall shape.",
         "Corners that are intentionally angled (more than the tolerance off) aren't flagged."),
        "orthogonalize", "Square the corners",
    ),
    CheckDef(
        "b_spike", BUILDING, "Spike / sharp vertex", MEDIUM,
        "A vertex makes a very sharp spike, usually from a double click or a vertex dragged by accident.",
        ("Zoom to the marker on the spike vertex.",
         "Delete that vertex with the Vertex tool, or run the auto-fix."),
        "remove_spikes", "Remove spike vertices",
        live=True,
    ),
    CheckDef(
        "b_on_road", BUILDING, "Building on road", HIGH,
        "The building overlaps the road corridor (road line plus buffer). Usually the building wasn't shifted "
        "to its base, the road is misaligned, or the building really encroaches on the road.",
        ("Compare with the imagery: is the building or the road in the wrong place?",
         "Shift the building to its base (Review mode), or move the road vertices to the road centre.",
         "If the building really encroaches, ignore this error and report it."),
        live=True,
    ),
    CheckDef(
        "b_road_setback", BUILDING, "Building very close to road", LOW,
        "The building is closer to the road centreline than the set-back distance.",
        ("Check the alignment of the road and the building against the imagery.",),
        default_on=False,
    ),
    CheckDef(
        "b_crosses_grid", BUILDING, "Building crosses grid edge", MEDIUM,
        "The building is cut by a grid or zone boundary. Horus assigns it to the grid that contains its "
        "centre, so only one reviewer owns it.",
        ("If the building belongs to your grid (its centre is inside), review it as usual.",
         "Coordinate with the neighbouring grid's reviewer so it isn't edited twice."),
        live=True,
    ),
    CheckDef(
        "b_outside_grid", BUILDING, "Building outside all grids", HIGH,
        "The building lies outside every grid cell, so nobody is assigned to review it.",
        ("Check whether the grid layer is complete.",
         "If the building is real, report it so the grid can be extended."),
    ),
    # ------------------------------------------------------------------ roads
    CheckDef(
        "r_null", ROAD, "Empty road", CRITICAL,
        "The road feature has no geometry.",
        ("Delete it, or digitise the road again.",),
        "delete", "Delete the empty feature",
    ),
    CheckDef(
        "r_invalid", ROAD, "Invalid road geometry", HIGH,
        "The line geometry is invalid (for example it has too few points).",
        ("Redraw the road, or run the auto-fix.",),
        "make_valid", "Repair with makeValid",
        live=True,
    ),
    CheckDef(
        "r_self_intersection", ROAD, "Road crosses itself", MEDIUM,
        "The road line loops over itself, often from a zig-zag while digitising.",
        ("Zoom to the marker at the crossing.",
         "Delete or move the vertices that make the loop."),
        live=True,
    ),
    CheckDef(
        "r_multipart", ROAD, "Multipart road", MEDIUM,
        "One road feature holds several disconnected lines.",
        ("Run the auto-fix to split the parts into separate features.",),
        "split_parts", "Split into separate roads",
    ),
    CheckDef(
        "r_duplicate", ROAD, "Duplicate road", HIGH,
        "Two road features run along the same line for almost their whole length.",
        ("Keep the better-aligned one and delete the other (auto-fix deletes the highlighted one).",),
        "delete", "Delete the duplicate road",
        live=True,
    ),
    CheckDef(
        "r_overlap", ROAD, "Roads overlapping", MEDIUM,
        "Two roads share part of their line. Roads should meet at a junction node, not run on top of each other.",
        ("Delete the doubled part from one of the roads, or merge them.",),
    ),
    CheckDef(
        "r_undershoot", ROAD, "Road stops short of junction", MEDIUM,
        "The road ends a few metres short of another road, so the network isn't connected.",
        ("Run the auto-fix: it moves the end point onto the other road and adds a junction vertex there.",
         "If it's a real dead end, ignore this error."),
        "snap_undershoot", "Snap end to the other road",
        live=True,
    ),
    CheckDef(
        "r_overshoot", ROAD, "Road overshoots junction", LOW,
        "The road crosses another road and continues a few metres past it, leaving a short tail.",
        ("Run the auto-fix to trim the tail at the junction.",
         "If the road really continues, extend it properly instead."),
        "trim_overshoot", "Trim the tail at the junction",
        live=True,
    ),
    CheckDef(
        "r_dead_end", ROAD, "Dead end", LOW,
        "The road end isn't connected to anything. Rural tracks often really are dead ends, "
        "so this is information only.",
        ("Check the imagery: does the road continue?",
         "Extend it if needed, otherwise ignore this error."),
    ),
    CheckDef(
        "r_isolated", ROAD, "Isolated road piece", MEDIUM,
        "A small group of roads isn't connected to the main road network.",
        ("Check whether it should connect to a nearby road and extend or snap it.",
         "Roads that touch the grid edge are skipped, because they may connect outside your grid."),
    ),
    CheckDef(
        "r_short", ROAD, "Very short road segment", LOW,
        "The segment is shorter than the minimum road length, usually left over from a split or a double click.",
        ("Delete it, or merge it with the adjacent road.",),
        "delete", "Delete the short segment",
    ),
]

CHECKS = {c.key: c for c in _CHECKS}
BUILDING_CHECKS = [c for c in _CHECKS if c.target == BUILDING]
ROAD_CHECKS = [c for c in _CHECKS if c.target == ROAD]
LIVE_CHECKS = {c.key for c in _CHECKS if c.live}
DEFAULT_ENABLED = {c.key for c in _CHECKS if c.default_on}
