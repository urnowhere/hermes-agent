from agent.side_task_policy import apply_side_task_timeout_cap, get_side_task_policy


def test_title_generation_is_observer_fail_open_with_short_cap():
    policy = get_side_task_policy("title_generation")

    assert policy.kind == "observer"
    assert policy.fail_open is True
    assert apply_side_task_timeout_cap("title_generation", 30.0) == 4.0


def test_compression_is_transforming_but_bounded():
    policy = get_side_task_policy("compression")

    assert policy.kind == "transforming"
    assert policy.fail_open is True
    assert apply_side_task_timeout_cap("compression", 60.0) == 20.0


def test_user_visible_tasks_keep_requested_timeout():
    assert get_side_task_policy("web_extract").kind == "blocking"
    assert apply_side_task_timeout_cap("web_extract", 45.0) == 45.0
