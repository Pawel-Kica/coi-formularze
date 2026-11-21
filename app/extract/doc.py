"""Legacy Word 97-2003 .doc -> raw text, parsed from the binary format ([MS-DOC]) with olefile.

What we read:
- the piece table (Clx in the table stream) to get the text of the main story, footnotes
  and text boxes (8-bit cp1252 or UTF-16 pieces);
- paragraph properties (PAPX FKPs) only to tell a table row end (fTtp) from a cell end,
  so tables come out as one line per row with cells joined by ' | ';
- text box positions (PlcSpaMom FSPA records) to read text boxes in visual order;
- fields: keep the displayed result, drop the instruction; FORMCHECKBOX becomes a box.
"""

import bisect
import io
import struct

import olefile

from app.extract.docx import format_number
from app.extract.layout import Box, boxes_to_lines


class DocError(ValueError):
    pass


def read_doc(data: bytes) -> str:
    ole = olefile.OleFileIO(io.BytesIO(data))
    try:
        if not ole.exists("WordDocument"):
            raise DocError("OLE file without a WordDocument stream (not a Word document)")
        wd = ole.openstream("WordDocument").read()
        if len(wd) < 0x22A or struct.unpack_from("<H", wd, 0)[0] != 0xA5EC:
            raise DocError("not a Word 97+ document")
        flags = struct.unpack_from("<H", wd, 0x0A)[0]
        if flags & 0x0100:
            raise DocError("encrypted .doc")
        table_name = "1Table" if flags & 0x0200 else "0Table"
        if not ole.exists(table_name):
            raise DocError("missing table stream")
        table = ole.openstream(table_name).read()
    finally:
        ole.close()
    return _Doc(wd, table).text()


def _fclcb(wd: bytes, index: int) -> tuple[int, int]:
    """FibRgFcLcb97 entry `index` -> (fc, lcb) in the table stream."""
    return struct.unpack_from("<II", wd, 154 + index * 8)


def _plc(buf: bytes, fc: int, lcb: int, data_size: int):
    """PLC structure -> (cps, data items). n = (lcb - 4) / (4 + data_size)."""
    n = (lcb - 4) // (4 + data_size)
    cps = list(struct.unpack_from(f"<{n + 1}i", buf, fc))
    base = fc + 4 * (n + 1)
    return cps, [buf[base + i * data_size : base + (i + 1) * data_size] for i in range(n)]


class _Doc:
    def __init__(self, wd: bytes, table: bytes):
        self.wd, self.table = wd, table
        ccp = struct.unpack_from("<12i", wd, 64)
        # FibRgLw97: ccpText, ccpFtn, ccpHdd, reserved, ccpAtn, ccpEdn, ccpTxbx, ccpHdrTxbx
        self.ccp_text, self.ccp_ftn, self.ccp_hdd = ccp[3], ccp[4], ccp[5]
        self.ccp_atn, self.ccp_edn, self.ccp_txbx = ccp[7], ccp[8], ccp[9]
        self.pieces = self._pieces()
        self.paras = self._paragraphs()
        self.lists = _Lists(wd, table)

    # --- piece table -------------------------------------------------------------
    def _pieces(self):
        fc, lcb = _fclcb(self.wd, 33)  # fcClx
        clx = self.table[fc : fc + lcb]
        i = 0
        while i < len(clx) and clx[i] == 0x01:  # Prc: skip property modifiers
            i += 3 + struct.unpack_from("<H", clx, i + 1)[0]
        if i >= len(clx) or clx[i] != 0x02:
            raise DocError("no piece table")
        lcb_pcdt = struct.unpack_from("<I", clx, i + 1)[0]
        cps, pcds = _plc(clx, i + 5, lcb_pcdt, 8)
        pieces = []
        for k, pcd in enumerate(pcds):
            fc_raw = struct.unpack_from("<I", pcd, 2)[0]
            compressed = bool(fc_raw & 0x40000000)
            fc_val = fc_raw & 0x3FFFFFFF
            pieces.append((cps[k], cps[k + 1], fc_val // 2 if compressed else fc_val, compressed))
        return pieces

    def chars(self, cp_start: int, cp_end: int) -> list[tuple[str, int]]:
        """Characters in [cp_start, cp_end) with their file offsets (needed for paragraph lookups)."""
        out = []
        for p_start, p_end, fc, compressed in self.pieces:
            lo, hi = max(cp_start, p_start), min(cp_end, p_end)
            if lo >= hi:
                continue
            if compressed:
                raw = self.wd[fc + (lo - p_start) : fc + (hi - p_start)]
                text = raw.decode("cp1252", errors="replace")
                out += [(c, fc + (lo - p_start) + j) for j, c in enumerate(text)]
            else:
                start = fc + 2 * (lo - p_start)
                raw = self.wd[start : start + 2 * (hi - lo)]
                text = raw.decode("utf-16-le", errors="replace")
                out += [(c, start + 2 * j) for j, c in enumerate(text)]
        return out

    # --- paragraph properties: table cells, row ends, list membership -------------
    def _paragraphs(self):
        """Sorted (fc_start, fc_end, props) for every paragraph run in the PAPX FKPs."""
        fc, lcb = _fclcb(self.wd, 13)  # fcPlcfBtePapx
        if not lcb:
            return []
        _, pns = _plc(self.table, fc, lcb, 4)
        ranges = []
        for pn in pns:
            page = struct.unpack_from("<I", pn)[0] & 0x3FFFFF
            fkp = self.wd[page * 512 : page * 512 + 512]
            if len(fkp) < 512:
                continue
            crun = fkp[511]
            rgfc = struct.unpack_from(f"<{crun + 1}I", fkp, 0)
            for i in range(crun):
                b_off = fkp[4 * (crun + 1) + 13 * i] * 2
                props = _papx_props(fkp, b_off) if b_off else _NO_PROPS
                ranges.append((rgfc[i], rgfc[i + 1], props))
        ranges.sort(key=lambda r: r[0])
        self.para_starts = [r[0] for r in ranges]
        return ranges

    def para_props(self, fc: int) -> dict:
        """Properties (in_table, row_end, ilfo, ilvl) of the paragraph holding file offset `fc`."""
        i = bisect.bisect_right(self.para_starts, fc) - 1
        if i >= 0 and self.paras[i][0] <= fc < self.paras[i][1]:
            return self.paras[i][2]
        return _NO_PROPS

    # --- text --------------------------------------------------------------------
    def story(self, cp_start: int, cp_end: int, inserts: dict[int, list[str]] | None = None) -> str:
        """Text of a CP range with fields resolved and table cells/rows marked.
        `inserts`: lines to emit right after the paragraph mark at a given CP (text boxes)."""
        out: list[str] = []
        stack: list[list] = []  # open fields: [instruction chars, in_result]
        notes = 0  # auto-numbered footnote marks (\x02) seen so far in this story
        at_start = True  # next visible char starts a paragraph (gets its list number)
        for k, (c, fc) in enumerate(self.chars(cp_start, cp_end)):
            if inserts and c in "\r\x07\x0c" and cp_start + k in inserts:
                out.append("\n" + "\n".join(inserts[cp_start + k]) + "\n")
            if c == "\x13":
                stack.append([[], False])
                continue
            if c == "\x14":
                if stack:
                    stack[-1][1] = True
                continue
            if c == "\x15":
                if stack:
                    instr = "".join(stack.pop()[0]).strip().upper()
                    if instr.startswith("FORMCHECKBOX"):
                        out.append("☐")
                continue
            if stack and not stack[-1][1]:
                stack[-1][0].append(c)
                continue
            if at_start and c not in "\r\x07\x0b\x0c":
                props = self.para_props(fc)
                if props["ilfo"] > 0:
                    label = self.lists.label(props["ilfo"], props["ilvl"])
                    if label:
                        out.append(label + " ")
            at_start = c in "\r\x07"
            if c == "\x07":
                out.append("\n" if self.para_props(fc)["row_end"] else "\t|\t")
            elif c == "\r":
                out.append(" " if self.para_props(fc)["in_table"] else "\n")  # paragraph inside a cell stays in the row
            elif c in "\x0b\x0c":
                out.append("\n")
            elif c == "\x02":
                notes += 1
                out.append(str(notes))
            elif c == "\x1e":
                out.append("-")
            elif c in "\x01\x03\x04\x05\x08\x1f":
                continue
            else:
                out.append(c)
        return _clean_cells("".join(out))

    def text(self) -> str:
        """Main story with text boxes after the paragraph they are anchored in, then footnotes."""
        cp = self.ccp_text + self.ccp_ftn + self.ccp_hdd + self.ccp_atn + self.ccp_edn
        inserts, loose = self._textboxes(cp) if self.ccp_txbx else ({}, [])
        main = self.story(0, self.ccp_text, inserts)
        cp = self.ccp_text
        footnotes = self.story(cp, cp + self.ccp_ftn) if self.ccp_ftn else ""
        return "\n".join(x for x in [main, "\n".join(loose), footnotes] if x.strip())

    def _textboxes(self, base_cp: int) -> tuple[dict[int, list[str]], list[str]]:
        """Text boxes as lines in visual order, keyed by the CP of the paragraph mark ending the
        paragraph they are anchored in (like DOCX). Boxes we cannot place go to the second list."""
        fc, lcb = _fclcb(self.wd, 56)  # fcPlcftxbxTxt
        if not lcb:
            return {}, [self.story(base_cp, base_cp + self.ccp_txbx)]
        cps, ftxbxs = _plc(self.table, fc, lcb, 22)
        texts = {}
        order = []
        for i, f in enumerate(ftxbxs):
            lid = struct.unpack_from("<i", f, 14)[0]
            t = self.story(base_cp + cps[i], base_cp + cps[i + 1]).strip()
            if t and lid not in texts:
                texts[lid] = t
                order.append(lid)
        # FSPA: spid, xaLeft, yaTop, xaRight, yaBottom (twips), anchored at a main-story CP.
        fc, lcb = _fclcb(self.wd, 40)  # fcPlcSpaMom
        pos = {}
        if lcb:
            ends = [i for i, (c, _) in enumerate(self.chars(0, self.ccp_text)) if c in "\r\x07\x0c"]
            anchors, fspas = _plc(self.table, fc, lcb, 26)
            for cp, f in zip(anchors, fspas):
                spid, xl, yt, xr, yb = struct.unpack_from("<i4i", f)
                k = bisect.bisect_left(ends, cp)
                pos[spid] = (ends[k] if k < len(ends) else -1, xl, yt, yb - yt)
        groups: dict[int, list[Box]] = {}
        loose = []
        for lid in order:
            if lid in pos and pos[lid][0] >= 0:
                end, x, y, h = pos[lid]
                groups.setdefault(end, []).append(Box(x, y, h, texts[lid]))
            else:
                loose.append(texts[lid])
        return {end: boxes_to_lines(boxes, tol=4 * 20) for end, boxes in groups.items()}, loose  # 4 pt in twips


_NO_PROPS = {"in_table": False, "row_end": False, "ilfo": 0, "ilvl": 0}


def _papx_props(fkp: bytes, off: int) -> dict:
    """Scan one PapxInFkp's sprms for the few paragraph properties we use."""
    cb = fkp[off]
    if cb == 0:
        size = fkp[off + 1] * 2
        start = off + 2
    else:
        size = cb * 2 - 1
        start = off + 1
    return _sprm_props(fkp[start + 2 : start + size])  # skip istd


def _sprm_props(grpprl: bytes) -> dict:
    props = dict(_NO_PROPS)
    i = 0
    while i + 2 <= len(grpprl):
        sprm = struct.unpack_from("<H", grpprl, i)[0]
        i += 2
        spra = sprm >> 13
        on = i < len(grpprl) and grpprl[i] != 0
        if sprm == 0x2416:  # sprmPFInTable
            props["in_table"] = on
        elif sprm in (0x2417, 0x244C):  # sprmPFTtp, sprmPFInnerTtp
            props["row_end"] = props["row_end"] or on
        elif sprm == 0x460B and i + 2 <= len(grpprl):  # sprmPIlfo
            props["ilfo"] = struct.unpack_from("<h", grpprl, i)[0]
        elif sprm == 0x260A and i < len(grpprl):  # sprmPIlvl
            props["ilvl"] = min(grpprl[i], 8)
        if spra in (0, 1):
            n = 1
        elif spra in (2, 4, 5):
            n = 2
        elif spra == 3:
            n = 4
        elif spra == 7:
            n = 3
        else:  # variable length
            if sprm in (0xD608, 0xD606):  # sprmTDefTable(10): 2-byte length
                if i + 2 > len(grpprl):
                    break
                n = struct.unpack_from("<H", grpprl, i)[0] + 1
            elif sprm == 0xC615 and i < len(grpprl) and grpprl[i] == 255:  # sprmPChgTabs, long form
                break
            else:
                if i >= len(grpprl):
                    break
                n = grpprl[i] + 1
        i += n
    return props


class _Lists:
    """Word 97 auto numbering (PlfLst + PlfLfo) -> list labels like '1.', 'a)', 'II.'."""

    NFC = {0: "decimal", 1: "upperRoman", 2: "lowerRoman", 3: "upperLetter", 4: "lowerLetter", 22: "decimal"}

    def __init__(self, wd: bytes, table: bytes):
        self.lfo_lsid: list[int] = []
        self.levels: dict[int, list[tuple[int, int, str]]] = {}  # lsid -> [(start, nfc, template)]
        self.counters: dict[int, list[int]] = {}
        try:
            self._parse(wd, table)
        except (struct.error, IndexError):
            self.lfo_lsid, self.levels = [], {}

    def _parse(self, wd, table):
        fc, lcb = _fclcb(wd, 73)  # fcPlfLst
        if not lcb:
            return
        n = struct.unpack_from("<h", table, fc)[0]
        lsts = []
        for k in range(n):
            base = fc + 2 + 28 * k
            lsid = struct.unpack_from("<i", table, base)[0]
            simple = table[base + 26] & 1
            lsts.append((lsid, 1 if simple else 9))
        pos = fc + lcb  # rgLvl follows PlfLst
        for lsid, count in lsts:
            levels = []
            for _ in range(count):
                start, nfc = struct.unpack_from("<iB", table, pos)
                cb_chpx, cb_papx = table[pos + 24], table[pos + 25]
                pos += 28 + cb_papx + cb_chpx
                cch = struct.unpack_from("<H", table, pos)[0]
                # number template; chars 0-8 are placeholders for that level's counter
                xst = table[pos + 2 : pos + 2 + 2 * cch].decode("utf-16-le", errors="replace")
                pos += 2 + 2 * cch
                levels.append((start, nfc, xst))
            self.levels[lsid] = levels
        fc, lcb = _fclcb(wd, 74)  # fcPlfLfo
        if lcb:
            m = struct.unpack_from("<i", table, fc)[0]
            self.lfo_lsid = [struct.unpack_from("<i", table, fc + 4 + 16 * k)[0] for k in range(m)]

    def label(self, ilfo: int, ilvl: int) -> str:
        if not 0 < ilfo <= len(self.lfo_lsid):
            return ""
        lsid = self.lfo_lsid[ilfo - 1]
        levels = self.levels.get(lsid)
        if not levels:
            return ""
        ilvl = min(ilvl, len(levels) - 1)
        counters = self.counters.setdefault(lsid, [0] * 9)
        counters[ilvl] = counters[ilvl] + 1 if counters[ilvl] else levels[ilvl][0]
        for deeper in range(ilvl + 1, 9):
            counters[deeper] = 0
        start, nfc, xst = levels[ilvl]
        if nfc not in self.NFC:
            return ""  # bullets (23), none (255) and exotic formats render as nothing
        out = []
        for ch in xst:
            lv = ord(ch)
            if lv < 9:
                value = counters[lv] or (levels[lv][0] if lv < len(levels) else 1)
                fmt = self.NFC.get(levels[lv][1] if lv < len(levels) else 0, "decimal")
                out.append(format_number(value, fmt))
            else:
                out.append(ch)
        return "".join(out).strip()


def _clean_cells(text: str) -> str:
    """Cell separators -> ' | ', trimming empty cells and a trailing separator on each row."""
    lines = []
    for line in text.split("\n"):
        if "\t|\t" in line:
            cells = [c.strip() for c in line.split("\t|\t")]
            line = " | ".join(c for c in cells if c)
        lines.append(line)
    return "\n".join(lines)
