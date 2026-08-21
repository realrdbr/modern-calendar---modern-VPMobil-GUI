from types import SimpleNamespace

from vp_data import collect_relevant_classes


def test_collect_relevant_classes_sorts_class_objects_by_kurzel():
    plan = SimpleNamespace(
        klassen={
            "10a": SimpleNamespace(kürzel="10a"),
            "2b": SimpleNamespace(kürzel="2b"),
            "2a": SimpleNamespace(kürzel="2a"),
        }
    )

    result = collect_relevant_classes(plan)

    assert [item.kürzel for item in result] == ["2a", "2b", "10a"]
