"""Catalogue of every Horus QC check: severity and plain-language guidance."""

from dataclasses import dataclass

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
    default_on: bool = True
    live: bool = False  # also run instantly after each edit


_CHECKS = [
    # ------------------------------------------------------------------ buildings
    CheckDef(
        "b_null", BUILDING, "Empty building", CRITICAL,
        "The feature has no geometry, so it can't be shown, measured or checked.",
        ("Open the attribute table and find the feature.",
         "Delete it, or draw its footprint again."),
    ),
    CheckDef(
        "b_invalid", BUILDING, "Invalid geometry", HIGH,
        "The outline is broken (for example a hole outside the building or too few corners), "
        "so areas and overlaps involving it are unreliable.",
        ("Zoom to the marker: it sits on the exact problem spot.",
         "Use the Vertex tool to move or delete the vertex causing it, or redraw the building."),
        live=True,
    ),
    CheckDef(
        "b_self_intersection", BUILDING, "Outline crosses itself", HIGH,
        "The outline crosses itself like a bow-tie, usually because two corners were clicked in the wrong order.",
        ("Zoom to the marker at the crossing point.",
         "With the Vertex tool, drag one of the crossing vertices back to its correct corner."),
        live=True,
    ),
    CheckDef(
        "b_multipart", BUILDING, "Several buildings in one feature", MEDIUM,
        "One feature holds several separate polygons. Each building should be its own feature.",
        ("Check that every part is a real building.",
         "Use Vector › Geometry Tools › Multipart to Singleparts, or redraw the extra parts as new features."),
        live=True,
    ),
    CheckDef(
        "b_duplicate", BUILDING, "Duplicate building", CRITICAL,
        "Two features cover almost the same footprint, usually a building drawn or pasted twice.",
        ("Compare the two outlines (the other one is dashed).",
         "Keep the better one and delete the other."),
        live=True,
    ),
    CheckDef(
        "b_contained", BUILDING, "Building inside another", HIGH,
        "A smaller building lies completely inside a bigger one, which is almost always a mistake.",
        ("Check the imagery: is it one building or two?",
         "One building: delete the inner feature.",
         "Two buildings: redraw them so they share a wall instead of overlapping."),
        live=True,
    ),
    CheckDef(
        "b_overlap", BUILDING, "Overlapping buildings", HIGH,
        "Two buildings overlap by a large share of the smaller one. Usually one was not shifted to its base "
        "or was shifted onto its neighbour.",
        ("Look at the imagery and decide which outline is wrong.",
         "Shift it to its base in Review mode (keys 1-9), or move its vertices."),
        live=True,
    ),
    CheckDef(
        "b_shared_wall", BUILDING, "Thin overlap on a shared wall", LOW,
        "Attached houses overlap by a thin sliver along their common wall, a small digitising slip.",
        ("Turn snapping on and move the wall vertices of one building onto the other's wall.",),
    ),
    CheckDef(
        "b_gap", BUILDING, "Tiny gap between buildings", LOW,
        "Two buildings almost touch but leave a hairline gap. Attached houses should usually share the wall.",
        ("Check the imagery: are the buildings attached?",
         "If so, turn snapping on and move the wall vertices onto the neighbour's wall.",
         "If there's a real alley, press Ignore."),
    ),
    CheckDef(
        "b_tiny", BUILDING, "Building too small", MEDIUM,
        "The footprint is smaller than the minimum building area: often a stray click or a leftover sliver.",
        ("Check the imagery.",
         "Delete it if it isn't a building, or redraw it at full size."),
        live=True,
    ),
    CheckDef(
        "b_huge", BUILDING, "Building unusually large", MEDIUM,
        "The footprint is larger than expected: maybe several buildings drawn as one, or a yard drawn by mistake.",
        ("Check the imagery.",
         "Split it into separate buildings with the Split Features tool if needed."),
    ),
    CheckDef(
        "b_not_square", BUILDING, "Corners almost square", LOW,
        "One or more corners are a few degrees off 90°. On rectangular buildings this is digitising wobble.",
        ("Move the marked corners with the Vertex tool, or redraw with the Rectangle tool.",
         "If the building really isn't rectangular, press Ignore."),
    ),
    CheckDef(
        "b_spike", BUILDING, "Spike in the outline", MEDIUM,
        "A vertex makes a very sharp spike, usually from a double click or a vertex dragged by accident.",
        ("Zoom to the marker on the spike.",
         "Delete that vertex with the Vertex tool."),
        live=True,
    ),
    CheckDef(
        "b_on_road", BUILDING, "Building on road", HIGH,
        "The building overlaps the road. Usually it wasn't shifted to its base, or the road is misaligned.",
        ("Compare with the imagery: is the building or the road in the wrong place?",
         "Shift the building to its base (Review mode), or move the road vertices.",
         "If the building really encroaches on the road, press Ignore and report it."),
        live=True,
    ),
    CheckDef(
        "b_road_setback", BUILDING, "Building very close to road", LOW,
        "The building is closer to the road centreline than the set-back distance.",
        ("Check the building and road positions against the imagery.",),
        default_on=False,
    ),
    CheckDef(
        "b_crosses_grid", BUILDING, "Building crosses grid edge", MEDIUM,
        "The building is cut by a grid boundary. It belongs to the grid that holds its centre.",
        ("If its centre is in your grid, review it as usual.",
         "Tell the neighbouring grid's reviewer so it isn't edited twice."),
        live=True,
    ),
    CheckDef(
        "b_outside_grid", BUILDING, "Building outside all grids", HIGH,
        "The building lies outside every grid cell, so nobody is assigned to review it.",
        ("Check that the grid layer is complete.",
         "If the building is real, report it so the grid can be extended."),
    ),
    # ------------------------------------------------------------------ roads
    CheckDef(
        "r_null", ROAD, "Empty road", CRITICAL,
        "The road feature has no geometry.",
        ("Delete it, or draw the road again.",),
    ),
    CheckDef(
        "r_invalid", ROAD, "Invalid road line", HIGH,
        "The road line is broken (for example it has too few points).",
        ("Redraw the road.",),
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
        "r_multipart", ROAD, "Several roads in one feature", MEDIUM,
        "One road feature holds several disconnected lines.",
        ("Use Vector › Geometry Tools › Multipart to Singleparts, or redraw the parts.",),
    ),
    CheckDef(
        "r_duplicate", ROAD, "Duplicate road", HIGH,
        "Two road features run along the same line for almost their whole length.",
        ("Keep the better-aligned one and delete the other.",),
        live=True,
    ),
    CheckDef(
        "r_overlap", ROAD, "Roads on top of each other", MEDIUM,
        "Two roads share part of their line. Roads should meet at a junction, not run on top of each other.",
        ("Remove the doubled part from one of the roads.",),
    ),
    CheckDef(
        "r_undershoot", ROAD, "Road stops short of junction", MEDIUM,
        "The road ends a few metres before another road, so the network isn't connected.",
        ("Turn snapping on and drag the road end onto the other road.",
         "If it's a real dead end, press Ignore."),
        live=True,
    ),
    CheckDef(
        "r_overshoot", ROAD, "Road runs past junction", LOW,
        "The road crosses another road and continues a few metres past it, leaving a short tail.",
        ("Delete the tail vertices so the road ends on the junction.",
         "If the road really continues, extend it properly."),
        live=True,
    ),
    CheckDef(
        "r_dead_end", ROAD, "Dead end", LOW,
        "The road end isn't connected to anything. Rural tracks often really are dead ends.",
        ("Check the imagery: does the road continue?",
         "Extend it if needed, otherwise press Ignore."),
    ),
    CheckDef(
        "r_isolated", ROAD, "Road not connected to network", MEDIUM,
        "A small group of roads isn't connected to the main road network.",
        ("Check whether it should join a nearby road, and connect it with snapping on.",),
    ),
    CheckDef(
        "r_short", ROAD, "Very short road piece", LOW,
        "The segment is shorter than the minimum road length, usually left over from a split or double click.",
        ("Delete it, or merge it with the road next to it.",),
    ),
]

CHECKS = {c.key: c for c in _CHECKS}
BUILDING_CHECKS = [c for c in _CHECKS if c.target == BUILDING]
ROAD_CHECKS = [c for c in _CHECKS if c.target == ROAD]
LIVE_CHECKS = {c.key for c in _CHECKS if c.live}
DEFAULT_ENABLED = {c.key for c in _CHECKS if c.default_on}
