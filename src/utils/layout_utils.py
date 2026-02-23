from PIL import ImageDraw


def draw_rounded_rect(draw, rect, radius, fill=None, outline=None, width=1):
    """Draw a rounded rectangle on a PIL ImageDraw canvas.

    Args:
        draw: PIL ImageDraw instance.
        rect: (x0, y0, x1, y1) bounding box coordinates.
        radius: Corner radius in pixels (clamped to half the smallest dimension).
        fill: Interior fill color (default None).
        outline: Border color (default None).
        width: Border width in pixels (default 1).
    """
    x0, y0, x1, y1 = rect
    # Clamp radius to half the smallest dimension
    max_radius = min((x1 - x0) // 2, (y1 - y0) // 2)
    radius = min(radius, max_radius)

    if radius <= 0:
        draw.rectangle(rect, fill=fill, outline=outline, width=width)
        return

    draw.rounded_rectangle(rect, radius=radius, fill=fill, outline=outline, width=width)


def draw_progress_bar(draw, position, size, progress, fill_color, bg_color,
                      border_color=None, border_width=1, radius=0):
    """Draw a horizontal progress bar.

    Args:
        draw: PIL ImageDraw instance.
        position: (x, y) top-left corner.
        size: (width, height) of the bar.
        progress: Fill fraction from 0.0 to 1.0 (clamped).
        fill_color: Color of the filled portion.
        bg_color: Color of the unfilled background.
        border_color: Optional border color (default None).
        border_width: Border thickness in pixels (default 1).
        radius: Corner radius for rounded bars (default 0).
    """
    x, y = position
    w, h = size
    progress = max(0.0, min(1.0, progress))

    # Background
    bg_rect = (x, y, x + w, y + h)
    if radius > 0:
        draw_rounded_rect(draw, bg_rect, radius, fill=bg_color)
    else:
        draw.rectangle(bg_rect, fill=bg_color)

    # Filled portion
    fill_w = int(w * progress)
    if fill_w > 0:
        fill_rect = (x, y, x + fill_w, y + h)
        if radius > 0:
            draw_rounded_rect(draw, fill_rect, radius, fill=fill_color)
        else:
            draw.rectangle(fill_rect, fill=fill_color)

    # Border
    if border_color:
        if radius > 0:
            draw_rounded_rect(draw, bg_rect, radius, outline=border_color, width=border_width)
        else:
            draw.rectangle(bg_rect, outline=border_color, width=border_width)


def calculate_grid(area, rows, cols, spacing=0):
    """Calculate evenly-spaced grid cell positions within an area.

    Args:
        area: (x, y, width, height) bounding rectangle for the grid.
        rows: Number of rows.
        cols: Number of columns.
        spacing: Gap in pixels between cells (default 0).

    Returns:
        List of (x, y, cell_width, cell_height) tuples, ordered row-major
        (left-to-right, top-to-bottom).
    """
    ax, ay, aw, ah = area
    cell_w = (aw - spacing * (cols - 1)) // cols if cols > 0 else aw
    cell_h = (ah - spacing * (rows - 1)) // rows if rows > 0 else ah
    cells = []
    for row in range(rows):
        for col in range(cols):
            cx = ax + col * (cell_w + spacing)
            cy = ay + row * (cell_h + spacing)
            cells.append((cx, cy, cell_w, cell_h))
    return cells


def draw_frame(draw, dimensions, frame_style, color, margin=None):
    """Draw a decorative frame overlay on a PIL image.

    Args:
        draw: PIL ImageDraw instance.
        dimensions: (width, height) of the image.
        frame_style: One of "None", "Corner", "Top and Bottom", "Rectangle".
        color: Frame color (e.g. "#000000" or RGB tuple).
        margin: Optional dict with top/bottom/left/right pixel insets.
    """
    if not frame_style or frame_style == "None":
        return

    w, h = dimensions
    m_top = (margin or {}).get("top", 0)
    m_bottom = (margin or {}).get("bottom", 0)
    m_left = (margin or {}).get("left", 0)
    m_right = (margin or {}).get("right", 0)

    # Scale line width to display size (roughly 3px at 1024w)
    line_w = max(2, int(w * 0.003))

    if frame_style == "Corner":
        # L-shaped corner brackets at top-left and bottom-right
        arm_x = int(w * 0.08)
        arm_y = int(h * 0.08)
        inset = line_w  # small inset so lines don't clip the edge

        # Top-left corner
        x0 = m_left + inset
        y0 = m_top + inset
        draw.line([(x0, y0 + arm_y), (x0, y0), (x0 + arm_x, y0)],
                  fill=color, width=line_w)

        # Bottom-right corner
        x1 = w - m_right - inset - 1
        y1 = h - m_bottom - inset - 1
        draw.line([(x1 - arm_x, y1), (x1, y1), (x1, y1 - arm_y)],
                  fill=color, width=line_w)

    elif frame_style == "Top and Bottom":
        # Thick horizontal bars at top and bottom
        bar_h = max(3, int(h * 0.02))

        # Top bar
        draw.rectangle(
            [m_left, m_top, w - m_right - 1, m_top + bar_h - 1],
            fill=color)

        # Bottom bar
        draw.rectangle(
            [m_left, h - m_bottom - bar_h, w - m_right - 1, h - m_bottom - 1],
            fill=color)

    elif frame_style == "Rectangle":
        # Full rounded-rectangle border
        inset = line_w
        radius = max(4, int(min(w, h) * 0.015))
        draw_rounded_rect(
            draw,
            (m_left + inset, m_top + inset,
             w - m_right - inset - 1, h - m_bottom - inset - 1),
            radius=radius,
            outline=color,
            width=line_w)


def draw_dotted_rect(draw, rect, dot_color, dot_spacing=5, dot_radius=1):
    """Fill a rectangle area with an evenly-spaced dot pattern.

    Useful for rendering unfilled portions of progress bars or decorative fills.

    Args:
        draw: PIL ImageDraw instance.
        rect: (x0, y0, x1, y1) bounding box to fill with dots.
        dot_color: Color of each dot.
        dot_spacing: Pixels between dot centers (default 5).
        dot_radius: Radius of each dot in pixels (default 1).
    """
    x0, y0, x1, y1 = rect
    y = y0 + dot_spacing // 2
    while y < y1:
        x = x0 + dot_spacing // 2
        while x < x1:
            draw.ellipse((x - dot_radius, y - dot_radius, x + dot_radius, y + dot_radius),
                         fill=dot_color)
            x += dot_spacing
        y += dot_spacing
