# ex:ts=4:et:

import dataclasses
import re
from gi.repository import Gdk, GLib, Gtk, GtkSource
from typing import List, Tuple

TOOLTIP_TEMPLATE = re.sub(r"\s+", " ", """
    {line}<span foreground="#008899">:</span>{column}<span foreground="#008899">:</span>
    <b>SC{code} <span foreground="{c}">({level})</span>:</b> {escapedmsg}
""".strip())


@dataclasses.dataclass
class Replacement:
    line: int
    endLine: int
    column: int
    endColumn: int
    start: Gtk.TextMark
    end: Gtk.TextMark
    text: str
    
    def get_range(self, buf: Gtk.TextBuffer) -> Tuple[int, int]:
        start = buf.get_iter_at_mark(self.start).get_offset()
        end = buf.get_iter_at_mark(self.end).get_offset()
        if start > end:
            start, end = end, start
        return start, end


class GutterRenderer(GtkSource.GutterRenderer):
    def __init__(self, view) -> None:
        GtkSource.GutterRenderer.__init__(self)
        
        self.view = view
        
        self.set_size(8)
        # self.set_padding(3, 0)
    
    def get_messages_in_range(self, line: int):
        return [
            msg
            for msg
            in self.view.context_data
            if (
                msg["file"] == "-"
                and line >= msg["line"]
                and line <= msg["endLine"]
            )
         ]
    
    def do_draw(self, cr, bg_area, cell_area, start, end, state):
        GtkSource.GutterRenderer.do_draw(self, cr, bg_area, cell_area, start, end, state)
        
        line = start.get_line() + 1
        
        if not self.view.context_data:
            return
        
        messages = self.get_messages_in_range(line)
        if not messages:
            return
        
        level = max(m["levelcls"] for m in messages)
        
        background = Gdk.RGBA()
        background.parse(level.color)
        Gdk.cairo_set_source_rgba(cr, background)
        cr.rectangle(cell_area.x, cell_area.y, cell_area.width, cell_area.height)
        cr.fill()
    
    def do_query_tooltip(self, it, area, x, y, tooltip):
        line_no = it.get_line() + 1
        
        if not self.view.context_data:
            return False
        
        messages = self.get_messages_in_range(line_no)
        if not messages:
            return False
        
        it_start = self.view.buffer.get_iter_at_line(line_no - 1)
        it_end = self.view.buffer.get_iter_at_line(line_no)
        line = self.view.buffer.get_text(it_start, it_end, True)
        
        text = "\n\n".join(
            self.format_message(message, line_no, line)
            for message
            in messages
        )
        
        tooltip.set_markup(f'<span font="monospace">{text}</span>')
        return True
    
    def format_message(self, message, line_no: int, line: str) -> str:
        content = TOOLTIP_TEMPLATE.format(
            c=message["levelcls"].color,
            escapedmsg=GLib.markup_escape_text(message["message"]),
            **message,
        )
        
        content += self.preview_note(message)
        content += self.preview_fix(message)
        return content
    
    def preview_note(self, message) -> str:
        note_start = self.view.buffer.get_iter_at_line_offset(message["line"] - 1, message["column"] - 1)
        note_end = self.view.buffer.get_iter_at_line_offset(message["endLine"] - 1, message["endColumn"] - 1)
        line_start = self.view.buffer.get_iter_at_line(message["line"] - 1)
        line_end = self.view.buffer.get_iter_at_line(message["endLine"])
        
        prefix = self.view.buffer.get_text(line_start, note_start, True)
        error = self.view.buffer.get_text(note_start, note_end, True)
        suffix = self.view.buffer.get_text(note_end, line_end, True).rstrip("\n")
        
        return (
            f'\n<span foreground="#999" background="#222">'
            f'{GLib.markup_escape_text(prefix)}'
            f'<u><span foreground="#F00">{GLib.markup_escape_text(error)}</span></u>'
            f'{GLib.markup_escape_text(suffix)}'
            f'</span>'
        )
    
    def preview_fix(self, message) -> str:
        if not message["fix"]:
            return ""
        if not message["fix"]["replacements"]:
            return ""
        
        buf = Gtk.TextBuffer()
        buf.set_text(self.view.buffer.get_text(
            self.view.buffer.get_start_iter(),
            self.view.buffer.get_end_iter(),
            True,
        ))
        
        replacements = []
        for item in message["fix"]["replacements"]:
            s = buf.create_mark(
                None,
                buf.get_iter_at_line_offset(item["line"] - 1, item["column"] - 1),
                True,
            )
            e = buf.create_mark(
                None,
                buf.get_iter_at_line_offset(item["endLine"] - 1, item["endColumn"] - 1),
                False,
            )
            replacements.append(Replacement(
                item["line"], item["endLine"],
                item["column"], item["endColumn"],
                s, e,
                item["replacement"],
            ))
        
        for replacement in replacements:
            buf.delete(buf.get_iter_at_mark(replacement.start), buf.get_iter_at_mark(replacement.end))
            buf.insert(buf.get_iter_at_mark(replacement.start), replacement.text)
        
        inserted_ranges = [
            replacement.get_range(buf)
            for replacement
            in replacements
        ]
        inserted_ranges = self.merge_ranges(inserted_ranges)
        
        pos = buf.get_iter_at_line(message["line"] - 1)
        content = ""
        
        for start, end in inserted_ranges:
            start_it = buf.get_iter_at_offset(start)
            end_it = buf.get_iter_at_offset(end)
            
            prefix = GLib.markup_escape_text(buf.get_text(pos, start_it, True))
            fix = GLib.markup_escape_text(buf.get_text(start_it, end_it, True))
            
            pos = end_it
            
            content += f'{prefix}<span foreground="#0F0">{fix}</span>'
        
        end_it = buf.get_iter_at_line(message["endLine"])
        suffix = GLib.markup_escape_text(buf.get_text(pos, end_it, True)).rstrip("\n")
        
        return (
            f'\n<span foreground="#0F0">Did you mean:</span>'
            f'\n<span foreground="#999" background="#222">'
            f'{content}{suffix}'
            f'</span>'
        )
    
    @staticmethod
    def merge_ranges(ranges: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
        "merges a list of text ranges and sorts them"
        
        ranges = sorted(ranges)
        
        new_ranges = [ranges[0]]
        for r in ranges:
            start, end = r
            
            if start >= new_ranges[-1][0] and start <= new_ranges[-1][1] and end >= new_ranges[-1][1]:
                new_ranges[-1] = (new_ranges[-1][0], end)
            else:
                new_ranges.append((start, end))
        
        return new_ranges
    
    def update(self):
        self.queue_draw()

