"""Default observation budgets apply after filtering, including action observations."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from iphone_use import SCHEMAS, TOOLS
from wda_controller import PhoneController
from test_controller import FakeWDA, node


class ObservationBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.client = FakeWDA()
        self.client.nodes = self.rows(250)
        self.phone = PhoneController(self.client, self.directory.name)

    @staticmethod
    def rows(count, y=300):
        return [node("Row " + str(index), y=y, kind="StaticText") for index in range(count)]

    def assert_budget(self, observed, returned=200, total=250):
        self.assertEqual(len(observed["nodes"]), returned)
        self.assertEqual(observed["total_nodes"], total)
        self.assertEqual(observed["truncated"], total > returned)

    def test_default_observation_returns_200_filtered_nodes(self):
        observed = self.phone.observe()
        self.assert_budget(observed)
        self.assertEqual(observed["nodes"][-1]["label"], "Row 199")
        self.assertEqual([call for call in self.client.calls if call[1].startswith("/source")], [
            ("GET", "/source?format=xml&excluded_attributes=visible,accessible", None),
        ])

    def test_typical_136_node_page_is_complete_without_explicit_budget(self):
        self.client.nodes = self.rows(136)
        self.assert_budget(self.phone.observe(), returned=136, total=136)
        self.assert_budget(self.phone.observe(max_nodes=100), returned=100, total=136)

    def test_explicit_budgets_still_override_the_default(self):
        for limit, returned in ((100, 100), (200, 200), (500, 250)):
            with self.subTest(max_nodes=limit):
                self.assert_budget(self.phone.observe(max_nodes=limit), returned=returned)

    def test_invisible_nodes_do_not_consume_the_default_budget(self):
        self.client.nodes = (
            [node("Outside " + str(index), y=2000) for index in range(125)]
            + [node("Hidden " + str(index), visible=False) for index in range(125)]
            + self.rows(250)
        )
        observed = self.phone.observe()
        self.assert_budget(observed)
        self.assertEqual(observed["nodes"][0]["label"], "Row 0")
        self.assertEqual(observed["nodes"][-1]["label"], "Row 199")

    def test_internal_observation_helpers_share_the_default_budget(self):
        nodes, viewport = self.phone.tree()
        helpers = {
            "state": lambda: self.phone.observation_from_state(nodes, viewport, self.client.app, "tree"),
            "post_action": lambda: self.phone.observe_after("tree"),
            "after": lambda: self.phone.after(observe="tree")["observation"],
            "scroll": lambda: self.phone.scroll_observation(nodes, viewport, "tree"),
        }
        for name, operation in helpers.items():
            with self.subTest(helper=name):
                self.assert_budget(operation())

    def test_tap_and_unverified_swipe_return_200_nodes(self):
        self.assert_budget(self.phone.tap(x=100, y=220, observe="tree")["observation"])
        self.assert_budget(self.phone.swipe(observe="tree")["observation"])

    def test_verified_swipe_reuses_full_tree_with_200_node_output(self):
        self.client.source_pages = [self.rows(250, y=300), self.rows(250, y=260)]
        result = self.phone.swipe(verify=True, observe="tree")
        self.assertTrue(result["progress_verified"])
        self.assert_budget(result["observation"])
        self.assertEqual(result["observation"]["nodes"][0]["rect"][1], 260)
        self.assertEqual(sum(path.startswith("/source") for _, path, _ in self.client.calls), 2)

    def test_batch_observation_and_actions_keep_the_default_budget(self):
        result = self.phone.batch([
            {"op": "observe", "args": {}},
            {"op": "tap", "args": {"x": 100, "y": 220, "observe": "tree"}},
            {"op": "swipe", "args": {"observe": "tree"}},
        ])
        self.assertTrue(result["complete"])
        self.assert_budget(result["results"][0])
        for action in result["results"][1:]:
            self.assert_budget(action["observation"])

    def test_model_schemas_publish_200_default_with_unchanged_bounds(self):
        observe_tool = next(tool for tool in TOOLS if tool["name"] == "pua_observe")
        batch_observe = next(
            step for step in SCHEMAS["batch"]["properties"]["steps"]["items"]["oneOf"]
            if step["properties"]["op"]["const"] == "observe"
        )["properties"]["args"]
        for schema in (SCHEMAS["observe"], observe_tool["inputSchema"], batch_observe):
            budget = schema["properties"]["max_nodes"]
            self.assertEqual(budget["default"], 200)
            self.assertEqual((budget["minimum"], budget["maximum"]), (1, 500))


if __name__ == "__main__":
    unittest.main()
