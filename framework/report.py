"""Plain summary report: one row per service, one column per check (+ cluster/host-level checks).

Written as reports/summary.txt and reports/summary.html and printed at the end of the console log.
The HTML uses no JavaScript, so it renders inside Jenkins' artifact viewer (which blocks scripts).
"""
import html
from collections import OrderedDict
from typing import Dict, List

SYMBOL = {"passed": "PASS", "failed": "FAIL", "error": "ERROR", "skipped": "SKIP"}


class Collector:
    def __init__(self):
        self.results: List[dict] = []

    def add(self, nodeid: str, name: str, params: dict, outcome: str, message: str):
        self.results.append({"nodeid": nodeid, "check": name, "params": params,
                             "outcome": outcome, "message": (message or "").strip()})

    # ---- shaping -------------------------------------------------------
    def service_matrix(self):
        checks: "OrderedDict[str, None]" = OrderedDict()
        rows: Dict[str, Dict[str, dict]] = {}
        for r in self.results:
            svc = r["params"].get("service")
            if not svc:
                continue
            checks[r["check"]] = None
            rows.setdefault(svc, {})[r["check"]] = r
        return list(checks), dict(sorted(rows.items()))

    def other_results(self):
        return [r for r in self.results if not r["params"].get("service")]

    def counts(self):
        out = {k: 0 for k in SYMBOL}
        for r in self.results:
            out[r["outcome"]] = out.get(r["outcome"], 0) + 1
        return out

    def problems(self):
        return [r for r in self.results if r["outcome"] in ("failed", "error")]

    # ---- renderers -----------------------------------------------------
    def text(self, title: str) -> str:
        checks, rows = self.service_matrix()
        c = self.counts()
        lines = [title, f"passed {c['passed']}  failed {c['failed']}  errors {c['error']}  skipped {c['skipped']}", ""]
        if rows:
            w = max(len(s) for s in rows) + 2
            short = [ch.replace("test_", "") for ch in checks]
            lines.append("SERVICE".ljust(w) + "  ".join(s.ljust(max(len(s), 5)) for s in short))
            for svc, cells in rows.items():
                vals = [SYMBOL.get(cells[ch]["outcome"], "-") if ch in cells else "-" for ch in checks]
                lines.append(svc.ljust(w) + "  ".join(v.ljust(max(len(s), 5)) for v, s in zip(vals, short)))
            lines.append("")
        other = self.other_results()
        if other:
            lines.append("CLUSTER / HOST CHECKS")
            for r in other:
                lines.append(f"  {SYMBOL[r['outcome']]:5} {r['check']}{_param_suffix(r)}")
            lines.append("")
        probs = self.problems()
        if probs:
            lines.append("PROBLEMS")
            for r in probs:
                svc = r["params"].get("service") or _param_suffix(r).strip(" []") or "cluster"
                lines.append(f"  [{svc}] {r['check']}: {_first_lines(r['message'], 6, '      ')}")
        return "\n".join(lines) + "\n"

    def html(self, title: str) -> str:
        checks, rows = self.service_matrix()
        c = self.counts()
        e = html.escape
        out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{e(title)}</title></head><body>",
               f"<h1>{e(title)}</h1>",
               f"<p><b>passed</b> {c['passed']} &nbsp; <b>failed</b> {c['failed']} &nbsp; "
               f"<b>errors</b> {c['error']} &nbsp; <b>skipped</b> {c['skipped']}</p>"]
        if rows:
            out.append("<h2>Services</h2><table border='1' cellpadding='4' cellspacing='0'><tr><th>Service</th>")
            out += [f"<th>{e(ch.replace('test_', ''))}</th>" for ch in checks]
            out.append("</tr>")
            for svc, cells in rows.items():
                out.append(f"<tr><td><b>{e(svc)}</b></td>")
                for ch in checks:
                    r = cells.get(ch)
                    if not r:
                        out.append("<td>-</td>")
                        continue
                    label = SYMBOL[r["outcome"]]
                    if r["outcome"] in ("failed", "error"):
                        label = f"<b>{label}</b>"
                    out.append(f"<td title='{e(r['message'][:300])}'>{label}</td>")
                out.append("</tr>")
            out.append("</table>")
        other = self.other_results()
        if other:
            out.append("<h2>Cluster / host checks</h2><table border='1' cellpadding='4' cellspacing='0'>"
                       "<tr><th>Result</th><th>Check</th></tr>")
            for r in other:
                out.append(f"<tr><td>{SYMBOL[r['outcome']]}</td><td>{e(r['check'] + _param_suffix(r))}</td></tr>")
            out.append("</table>")
        probs = self.problems()
        if probs:
            out.append("<h2>Problems</h2>")
            for r in probs:
                svc = r["params"].get("service") or _param_suffix(r).strip(" []") or "cluster"
                out.append(f"<h3>[{e(svc)}] {e(r['check'])}</h3><pre>{e(r['message'][:4000])}</pre>")
        skips = [r for r in self.results if r["outcome"] == "skipped"]
        if skips:
            out.append("<h2>Skipped</h2><ul>")
            out += [f"<li>{e(r['check'] + _param_suffix(r))}: {e(r['message'][:200])}</li>" for r in skips]
            out.append("</ul>")
        out.append("</body></html>")
        return "\n".join(out)


def _param_suffix(r) -> str:
    p = {k: v for k, v in r["params"].items() if k != "service"}
    return f" [{', '.join(str(v) for v in p.values())}]" if p else ""


def _first_lines(text: str, n: int, indent: str) -> str:
    lines = [l for l in (text or "").splitlines() if l.strip()][:n]
    return ("\n" + indent).join(lines)
