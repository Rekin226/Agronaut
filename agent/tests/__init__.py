def session_keys(app):
    """Snapshot AppTest state across its old and new mapping interfaces."""
    state = app.session_state
    if hasattr(state, "to_dict"):
        return state.to_dict().keys()
    return state.filtered_state.keys()
