# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0


def test_observer_state_collector_valid_and_failure_keeps_last_values(registry, render_prometheus):
    from openviking.metrics.collectors.observer_state import ObserverStateCollector

    class S:
        def __init__(self, ok: bool, err: bool):
            self.is_healthy = ok
            self.has_errors = err

    class DS:
        def __init__(self):
            self.fail = False

        def read_component_states(self):
            if self.fail:
                raise RuntimeError("boom")
            return {"a": S(True, False), "b": S(False, True), "c": S(True, True)}

    ds = DS()
    c = ObserverStateCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_observer_components_total{valid="1"} 3.0' in text
    assert 'openviking_observer_components_unhealthy{valid="1"} 1.0' in text
    assert 'openviking_observer_components_with_errors{valid="1"} 2.0' in text

    ds.fail = True
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_observer_components_total{valid="0"} 3.0' in text2
    assert 'openviking_observer_components_unhealthy{valid="0"} 1.0' in text2
    assert 'openviking_observer_components_with_errors{valid="0"} 2.0' in text2


def test_model_usage_collector_delta_and_available_gauge(registry, render_prometheus):
    from openviking.metrics.collectors.model_usage import ModelUsageCollector

    class DS:
        def __init__(self):
            self.data = {
                "vlm": {
                    "available": True,
                    "usage_by_model": {
                        "m1": {
                            "usage_by_provider": {
                                "p1": {
                                    "prompt_tokens": 2,
                                    "completion_tokens": 3,
                                    "total_tokens": 5,
                                    "call_count": 1,
                                }
                            }
                        }
                    },
                },
                "embedding": {
                    "available": False,
                    "usage_by_model": {},
                },
                "rerank": {
                    "available": False,
                    "usage_by_model": {},
                },
            }

        def read_model_usage(self):
            return self.data

    ds = DS()
    c = ModelUsageCollector(data_source=ds)
    c.collect(registry)
    text = render_prometheus(registry)
    assert 'openviking_model_usage_available{model_type="vlm",valid="1"} 1.0' in text
    assert 'openviking_model_usage_available{model_type="embedding",valid="1"} 0.0' in text
    assert 'openviking_model_usage_available{model_type="rerank",valid="1"} 0.0' in text
    assert "openviking_model_usage_valid" not in text
    assert 'openviking_model_calls_total{model_name="m1",model_type="vlm",provider="p1"} 1' in text
    assert (
        'openviking_model_tokens_total{model_name="m1",model_type="vlm",provider="p1",token_type="total"} 5'
        in text
    )

    ds.data["vlm"]["usage_by_model"]["m1"]["usage_by_provider"]["p1"]["call_count"] = 2
    ds.data["vlm"]["usage_by_model"]["m1"]["usage_by_provider"]["p1"]["total_tokens"] = 7
    ds.data["vlm"]["usage_by_model"]["m1"]["usage_by_provider"]["p1"]["prompt_tokens"] = 3
    ds.data["vlm"]["usage_by_model"]["m1"]["usage_by_provider"]["p1"]["completion_tokens"] = 4
    c.collect(registry)
    text2 = render_prometheus(registry)
    assert 'openviking_model_calls_total{model_name="m1",model_type="vlm",provider="p1"} 2' in text2
    assert (
        'openviking_model_tokens_total{model_name="m1",model_type="vlm",provider="p1",token_type="total"} 7'
        in text2
    )


def test_model_usage_collector_failure_reuses_last_available_state_with_valid_zero(
    registry, render_prometheus
):
    from openviking.metrics.collectors.model_usage import ModelUsageCollector

    class DS:
        def __init__(self):
            self.fail = False

        def read_model_usage(self):
            if self.fail:
                raise RuntimeError("boom")
            return {
                "vlm": {"available": True, "usage_by_model": {}},
                "embedding": {"available": False, "usage_by_model": {}},
                "rerank": {"available": False, "usage_by_model": {}},
            }

    ds = DS()
    collector = ModelUsageCollector(data_source=ds)

    collector.collect(registry)
    ds.fail = True
    collector.collect(registry)

    text = render_prometheus(registry)
    assert 'openviking_model_usage_available{model_type="vlm",valid="0"} 1.0' in text
    assert 'openviking_model_usage_available{model_type="embedding",valid="0"} 0.0' in text
    assert 'openviking_model_usage_available{model_type="rerank",valid="0"} 0.0' in text
