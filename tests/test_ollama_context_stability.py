"""Guard the invariant that fixed the multi-user stuttering.

Ollama allocates a model's KV cache at load time. Sending a different `num_ctx`
makes it tear the model down and reload with the new window, and a reload
blocks every other in-flight request. Measured on the deployment box
(gemma4, 2x RTX 5090):

    same num_ctx repeated ....... 0.61 s
    num_ctx changed ............. 7.8  s   every time

The code used to derive `num_ctx` per request (1024 for greetings, 2048 for
short questions, 6144 for web mode, a profile value otherwise) and hardcoded
2048 in two helper calls that run on the *same* model as the chat. A mixed
multi-user workload therefore reloaded the model on nearly every turn.

These tests fail if anyone reintroduces a per-request or hardcoded window.
"""

import ast
import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SERVER = os.path.join(_ROOT, "server.py")


@pytest.fixture(scope="module")
def server_source():
    with open(_SERVER, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def server_tree(server_source):
    return ast.parse(server_source)


def _num_ctx_values(tree):
    """Every value assigned to a "num_ctx" key anywhere in server.py."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "num_ctx":
                found.append(value)
    return found


class TestNumCtxIsConstant:
    def test_no_hardcoded_context_window(self, server_tree):
        """A literal num_ctx means that call site can differ from the others."""
        literals = [
            v.value for v in _num_ctx_values(server_tree)
            if isinstance(v, ast.Constant) and isinstance(v.value, int)
        ]
        assert not literals, (
            f"hardcoded num_ctx values {literals} found. Every call must use the "
            f"single configured window, or Ollama reloads the model between calls."
        )

    def test_every_call_site_uses_the_same_source(self, server_tree):
        """All num_ctx values must trace back to cfg.ollama_num_ctx."""
        exprs = [ast.unparse(v) for v in _num_ctx_values(server_tree)]
        assert exprs, "no num_ctx settings found — did the options dicts move?"
        allowed = {"cfg.ollama_num_ctx", "_ctx"}
        unexpected = [e for e in exprs if e not in allowed]
        assert not unexpected, f"num_ctx set from unexpected expressions: {unexpected}"

    def test_ctx_variable_is_assigned_once_from_config(self, server_source):
        """`_ctx` must be a plain read of the config, not a branch."""
        assignments = re.findall(r"^\s*_ctx\s*=\s*(.+)$", server_source, re.M)
        assert assignments, "_ctx assignment not found"
        assert assignments == ["cfg.ollama_num_ctx"], (
            f"_ctx is assigned {assignments}; it must be the configured window "
            f"only. Branching on mode/length is what caused the reload stalls."
        )

    def test_no_mode_dependent_context_branches(self, server_source):
        """Catch the specific pattern that regressed before."""
        for pattern in [
            r"_ctx\s*=\s*1024",
            r"_ctx\s*=\s*2048",
            r"_ctx\s*=\s*min\(",
            r"_ctx\s*=\s*response_profile",
        ]:
            assert not re.search(pattern, server_source), (
                f"per-request context sizing reintroduced: {pattern}"
            )


class TestKeepAliveIsConsistent:
    def test_all_keep_alive_values_match(self, server_source):
        """A short keep_alive on a helper can evict the chat model mid-conversation."""
        values = re.findall(r'keep_alive="([^"]+)"', server_source)
        assert values, "no keep_alive settings found"
        assert len(set(values)) == 1, (
            f"inconsistent keep_alive values {sorted(set(values))}: the shortest "
            f"one decides when the shared model gets unloaded."
        )


class TestConfigExposesTheWindow:
    def test_config_property_exists(self):
        import sys
        sys.path.insert(0, _ROOT)
        from config_loader import cfg
        value = cfg.ollama_num_ctx
        assert isinstance(value, int) and value > 0
        # KV cache scales with num_ctx x OLLAMA_NUM_PARALLEL; a runaway value
        # would not fit in VRAM.
        assert value <= 131072, f"num_ctx {value} is implausibly large"
