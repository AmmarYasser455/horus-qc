"""Small helpers for editing layers safely."""


class EditError(Exception):
    """A user-facing reason why an edit couldn't be made."""


def ensure_editable(layer):
    """Turn on editing for ``layer`` (all changes stay undoable until saved)."""
    if layer.isEditable():
        return
    if not layer.startEditing():
        raise EditError("Layer '%s' can't be edited (read-only?)." % layer.name())
