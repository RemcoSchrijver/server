import asyncio

import pytest

from tests.utils import fast_forward

from .conftest import connect_and_sign_in, read_until, read_until_command
from .test_game import get_player_ratings, send_player_options

pytestmark = pytest.mark.asyncio


async def queue_players_for_matchmaking(lobby_server):
    res = await asyncio.gather(*[
        connect_and_sign_in(
            (f"ladder{i}",) * 2,
            lobby_server
        )
        for i in range(1, 5)
    ])
    protos = [proto for _, _, proto in res]
    ids = [id_ for id_, _, _ in res]

    await asyncio.gather(*[
        read_until_command(proto, "game_info") for proto in protos
    ])

    await asyncio.gather(*[
        proto.send_message({
            "command": "game_matchmaking",
            "state": "start",
            "faction": "uef",
            "queue_name": "tmm2v2"
        })
        for proto in protos
    ])

    # If the players did not match, this will fail due to a timeout error
    await asyncio.gather(*[
        read_until_command(proto, "match_found") for proto in protos
    ])

    return protos, ids


async def client_response(proto):
    msg = await read_until_command(proto, "game_launch")
    # Ensures that the game enters the `LOBBY` state
    await proto.send_message({
        "command": "GameState",
        "target": "game",
        "args": ["Idle"]
    })
    # Ensures that the game is considered hosted
    await proto.send_message({
        "command": "GameState",
        "target": "game",
        "args": ["Lobby"]
    })
    return msg


@fast_forward(10)
async def test_info_message(lobby_server):
    _, _, proto = await connect_and_sign_in(
        ("ladder1", "ladder1"), lobby_server
    )

    await read_until_command(proto, "game_info")

    await proto.send_message({
        "command": "game_matchmaking",
        "state": "start",
        "faction": "uef",
        "mod": "tmm2v2"
    })

    msg = await read_until_command(proto, "matchmaker_info")

    assert msg["queues"]
    for queue in msg["queues"]:
        boundaries = queue["boundary_80s"]

        if queue["queue_name"] == "tmm2v2":
            assert boundaries == [[1300, 1700]]
        else:
            assert boundaries == []


@fast_forward(10)
async def test_game_matchmaking(lobby_server):
    protos, _ = await queue_players_for_matchmaking(lobby_server)

    msgs = await asyncio.gather(*[client_response(proto) for proto in protos])

    uid = set(msg["uid"] for msg in msgs)
    assert len(uid) == 1
    for msg in msgs:
        assert msg["init_mode"] == 1
        assert "None" not in msg["name"]
        assert msg["mod"] == "faf"
        assert msg["expected_players"] == 4
        assert msg["team"] in (2, 3)
        assert msg["map_position"] in (1, 2, 3, 4)
        assert msg["faction"] == 1


@fast_forward(60)
async def test_game_matchmaking_timeout(lobby_server):
    protos, _ = await queue_players_for_matchmaking(lobby_server)

    # We don't send the `GameState: Lobby` command so the game should time out
    await asyncio.gather(*[
        read_until_command(proto, "match_cancelled") for proto in protos
    ])


@fast_forward(60)
async def test_game_ratings(lobby_server):
    protos, ids = await queue_players_for_matchmaking(lobby_server)

    msgs = await asyncio.gather(*[client_response(proto) for proto in protos])
    # Configure. Just send options for all players so we don't need to worry
    # about who is the host.
    for proto in protos:
        for player_id, msg in zip(ids, msgs):
            slot = msg["map_position"]
            await send_player_options(
                proto,
                [player_id, "Army", slot],
                [player_id, "Color", slot],
                [player_id, "Faction", msg["faction"]],
                [player_id, "StartSpot", slot],
                [player_id, "Team", msg["team"]],
            )
    # Launch
    await asyncio.gather(*[proto.send_message({
        "command": "GameState",
        "target": "game",
        "args": ["Launching"]
    }) for proto in protos])

    await asyncio.gather(*[read_until(
        proto,
        lambda cmd: cmd["command"] == "game_info" and cmd["launched_at"]
    ) for proto in protos])

    # Report results
    for proto in protos:
        for result in (
            [1, "victory 10"],
            [2, "defeat -10"],
            [3, "victory 10"],
            [4, "defeat -10"]
        ):
            await proto.send_message({
                "target": "game",
                "command": "GameResult",
                "args": result
            })
    for proto in protos:
        await proto.send_message({
            "target": "game",
            "command": "GameEnded",
            "args": []
        })

    new_ratings = await get_player_ratings(
        protos[0],
        *[f"ladder{i}" for i in range(1, 5)],
        rating_type="tmm_2v2"
    )
    for _, rating in new_ratings.items():
        assert rating != (1500, 500)


@fast_forward(60)
async def test_game_ratings_initialized_based_on_global(lobby_server):
    _, _, proto = await connect_and_sign_in(
        ("test", "test_password"), lobby_server
    )

    msg = await read_until_command(proto, "player_info")
    assert msg == {
        "command": "player_info",
        "players": [
            {
                "id": 1,
                "login": "test",
                "clan": "678",
                "country": "",
                "ratings": {
                    "global": {
                        "rating": [2000.0, 125.0],
                        "number_of_games": 5
                    },
                    "ladder_1v1": {
                        "rating": [2000.0, 125.0],
                        "number_of_games": 5
                    }
                },
                "global_rating": [2000.0, 125.0],
                "ladder_rating": [2000.0, 125.0],
                "number_of_games": 5,
            }
        ]
    }

    await proto.send_message({
        "command": "game_matchmaking",
        "state": "start",
        "faction": "uef",
        "mod": "tmm2v2"
    })

    msg = await read_until(proto, lambda msg: (
        msg["command"] == "player_info" and
        "tmm_2v2" in msg["players"][0]["ratings"]
    ))
    assert msg == {
        "command": "player_info",
        "players": [
            {
                "id": 1,
                "login": "test",
                "clan": "678",
                "country": "",
                "ratings": {
                    "global": {
                        "rating": [2000.0, 125.0],
                        "number_of_games": 5
                    },
                    "ladder_1v1": {
                        "rating": [2000.0, 125.0],
                        "number_of_games": 5
                    },
                    "tmm_2v2": {
                        "rating": [2000.0, 250.0],
                        "number_of_games": 0
                    }
                },
                "global_rating": [2000.0, 125.0],
                "ladder_rating": [2000.0, 125.0],
                "number_of_games": 5,
            }
        ]
    }
