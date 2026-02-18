from PIL import ImageDraw, ImageFont


def wrap_text(draw, text, font, max_width):
    """Wrap text to fit within max_width, returning list of lines.

    Args:
        draw: PIL ImageDraw instance (used for text measurement).
        text: The string to wrap.
        font: PIL ImageFont to measure with.
        max_width: Maximum pixel width per line.

    Returns:
        List of strings, one per wrapped line. Single words that exceed
        max_width are kept intact on their own line.
    """
    if not text:
        return []

    words = text.split()
    lines = []
    current_line = ""

    for word in words:
        test_line = f"{current_line} {word}".strip() if current_line else word
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            # If a single word exceeds max_width, add it anyway
            current_line = word
    if current_line:
        lines.append(current_line)

    return lines


def truncate_text(draw, text, font, max_width, suffix="..."):
    """Truncate text to fit within max_width, adding suffix if truncated.

    Args:
        draw: PIL ImageDraw instance.
        text: The string to truncate.
        font: PIL ImageFont to measure with.
        max_width: Maximum pixel width allowed.
        suffix: String appended when truncation occurs (default "...").

    Returns:
        The original text if it fits, otherwise the longest prefix + suffix
        that fits within max_width.
    """
    if not text:
        return ""

    bbox = draw.textbbox((0, 0), text, font=font)
    if bbox[2] - bbox[0] <= max_width:
        return text

    for i in range(len(text), 0, -1):
        truncated = text[:i].rstrip() + suffix
        bbox = draw.textbbox((0, 0), truncated, font=font)
        if bbox[2] - bbox[0] <= max_width:
            return truncated

    return suffix


def draw_multiline_text(draw, text, position, font, fill, max_width,
                        line_spacing=4, align="left"):
    """Wrap text and draw it, returning the total height used.

    Args:
        draw: PIL ImageDraw instance.
        text: The string to wrap and draw.
        position: (x, y) top-left origin for the text block.
        font: PIL ImageFont to render with.
        fill: Text color (RGB tuple or hex string).
        max_width: Maximum pixel width for wrapping.
        line_spacing: Extra pixels between lines (default 4).
        align: "left", "center", or "right" within max_width.

    Returns:
        Total pixel height consumed by the rendered text block.
    """
    lines = wrap_text(draw, text, font, max_width)
    x, y = position
    total_height = 0

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_height = bbox[3] - bbox[1]
        line_width = bbox[2] - bbox[0]

        if align == "center":
            line_x = x + (max_width - line_width) // 2
        elif align == "right":
            line_x = x + max_width - line_width
        else:
            line_x = x

        draw.text((line_x, y + total_height), line, font=font, fill=fill)
        total_height += line_height + line_spacing

    return total_height


def measure_text_block(draw, text, font, max_width, line_spacing=4):
    """Measure the total height a wrapped text block would occupy.

    Args:
        draw: PIL ImageDraw instance.
        text: The string to measure.
        font: PIL ImageFont to measure with.
        max_width: Maximum pixel width for wrapping.
        line_spacing: Extra pixels between lines (default 4).

    Returns:
        Total pixel height the text block would occupy if drawn.
    """
    lines = wrap_text(draw, text, font, max_width)
    if not lines:
        return 0

    total_height = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        total_height += (bbox[3] - bbox[1]) + line_spacing

    # Remove trailing line_spacing
    return total_height - line_spacing if lines else 0


def get_text_dimensions(draw, text, font):
    """Get pixel width and height of a text string.

    Note: Uses textbbox() which can underreport visual height for large fonts.
    For vertical spacing of large text (font size factors >= 0.06 of display
    height), use int(font_size * 1.15) instead of the returned height.
    Width measurement is accurate at all sizes.

    Args:
        draw: PIL ImageDraw instance.
        text: The string to measure.
        font: PIL ImageFont to measure with.

    Returns:
        (width, height) tuple in pixels.
    """
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]
