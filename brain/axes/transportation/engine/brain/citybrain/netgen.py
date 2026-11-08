"""Builds the big city road network: an N x N grid of signalised crossings (default 6 x 6 = 36 signals, 200 m blocks).

The crossings are built to be the SAME kind of junction as the 2x2 grid the agent was first trained on
(4 approaches, 2 lanes each, 3 links per approach, and the same 4-green-phase program), so the saved training
transfers to every signal of the big map.

    python -m citybrain.netgen            # writes world/city.net.xml
"""
import os
import re
import shutil
import subprocess
import sys

N = 6
BLOCK = 200.0
SPEED = 13.89

PROGRAM = """<tlLogic id="{id}" type="static" programID="0" offset="{offset}">
        <phase duration="33" state="GGrrrrGGrrrr"/>
        <phase duration="3" state="yyrrrryyrrrr"/>
        <phase duration="6" state="rrGrrrrrGrrr"/>
        <phase duration="3" state="rryrrrrryrrr"/>
        <phase duration="33" state="rrrGGrrrrGGr"/>
        <phase duration="3" state="rrryyrrrryyr"/>
        <phase duration="6" state="rrrrrGrrrrrG"/>
        <phase duration="3" state="rrrrryrrrrry"/>
    </tlLogic>"""


def node_id(i, j):
    return f"J{i}_{j}" if 1 <= i <= N and 1 <= j <= N else f"B{i}_{j}"


def edge_id(a, b):
    return f"{node_id(*a)}__{node_id(*b)}"


def _is_corner(i, j):
    return i in (0, N + 1) and j in (0, N + 1)


def _is_inner(i, j):
    return 1 <= i <= N and 1 <= j <= N


def plain_files(out_dir):
    nodes, edges, cons = [], [], []
    for i in range(N + 2):
        for j in range(N + 2):
            if _is_corner(i, j):
                continue
            nodes.append(f'<node id="{node_id(i, j)}" x="{i * BLOCK}" y="{j * BLOCK}" type="{"traffic_light" if _is_inner(i, j) else "dead_end"}"/>')
    for i in range(N + 2):
        for j in range(N + 2):
            if _is_corner(i, j):
                continue
            for di, dj in ((1, 0), (0, 1)):
                a, b = (i, j), (i + di, j + dj)
                if b[0] > N + 1 or b[1] > N + 1 or _is_corner(*b):
                    continue
                if not _is_inner(*a) and not _is_inner(*b):
                    continue
                for p, q in ((a, b), (b, a)):
                    edges.append(f'<edge id="{edge_id(p, q)}" from="{node_id(*p)}" to="{node_id(*q)}" numLanes="2" speed="{SPEED}"/>')
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1)]
    for i in range(1, N + 1):
        for j in range(1, N + 1):
            for d in dirs:                       # d = travel direction of the incoming edge
                src = (i - d[0], j - d[1])
                frm = edge_id(src, (i, j))
                for kind, dd, lane in (("s", d, 0), ("r", (d[1], -d[0]), 0), ("l", (-d[1], d[0]), 1)):
                    dst = (i + dd[0], j + dd[1])
                    cons.append(f'<connection from="{frm}" to="{edge_id((i, j), dst)}" fromLane="{lane}" toLane="{0 if kind == "s" else lane}"/>')
    nod, edg, con = (os.path.join(out_dir, f) for f in ("city.nod.xml", "city.edg.xml", "city.con.xml"))
    for path, tag, items in ((nod, "nodes", nodes), (edg, "edges", edges), (con, "connections", cons)):
        with open(path, "w") as fh:
            fh.write(f"<{tag}>\n" + "\n".join(items) + f"\n</{tag}>\n")
    return nod, edg, con


def build(out_dir="world", net_name="city.net.xml"):
    os.makedirs(out_dir, exist_ok=True)
    nod, edg, con = plain_files(out_dir)
    out = os.path.join(out_dir, net_name)
    exe = shutil.which("netconvert")
    if exe is None:
        import sumolib
        exe = sumolib.checkBinary("netconvert")
    subprocess.check_call([exe, "-n", nod, "-e", edg, "-x", con, "-o", out, "--no-turnarounds", "true", "--no-warnings"],
                          stdout=subprocess.DEVNULL)
    with open(out) as fh:
        net = fh.read()
    net = re.sub(r'<tlLogic id="([^"]+)"[^>]*>.*?</tlLogic>',
                 lambda m: PROGRAM.format(id=m.group(1), offset=0), net, flags=re.S)
    with open(out, "w") as fh:
        fh.write(net)
    for f in (nod, edg, con):
        os.remove(f)
    return out


if __name__ == "__main__":
    print("written", build(sys.argv[1] if len(sys.argv) > 1 else "world"))
