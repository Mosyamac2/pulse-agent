"""Eight employee archetypes with parameter packs.

Each archetype carries the means / variances / probabilities used by `seed.py`
and `tick.py` to draw realistic correlated metrics. Numbers are intentionally
chosen so the trained ML models pick up signal — see §7.2 of the TZ.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Archetype:
    name: str
    share: int                              # share of 100 employees
    # Daily metrics (means)
    tasks_done_mean: float
    tasks_done_std: float
    hours_logged_mean: float
    meetings_mean: float
    focus_mean: float                       # 0..1
    switches_mean: float                    # switches per minute
    working_hours_mean: float
    # Wearables
    sleep_h_mean: float
    stress_mean: float                      # 0..1
    steps_mean: float
    # Career
    perf_score_mean: float                  # 1..5
    perf_score_std: float
    promotion_probability_per_year: float
    termination_probability_36m: float
    # Peer feedback
    peer_sentiment_mean: float              # -1..+1
    peer_volume_per_month: float
    cooperation_360_mean: float             # 0..1 (low for toxic_high_performer)
    # Course completion
    course_complete_rate: float             # 0..1
    # Connectivity
    edge_density: float                     # weight cap factor
    # Special triggers
    burnout_prone: bool = False
    isolated: bool = False
    overworked: bool = False
    toxic: bool = False
    leader: bool = False


ARCHETYPES: list[Archetype] = [
    Archetype(
        name="newbie_enthusiast", share=15,
        tasks_done_mean=6.5, tasks_done_std=2.0, hours_logged_mean=8.5,
        meetings_mean=2.5, focus_mean=0.55, switches_mean=2.5, working_hours_mean=8.0,
        sleep_h_mean=7.2, stress_mean=0.35, steps_mean=8500,
        perf_score_mean=3.4, perf_score_std=0.6, promotion_probability_per_year=0.18,
        termination_probability_36m=0.04,
        peer_sentiment_mean=0.45, peer_volume_per_month=2.5,
        cooperation_360_mean=0.78, course_complete_rate=0.85, edge_density=0.7,
    ),
    Archetype(
        name="tired_midfielder", share=25,
        tasks_done_mean=4.2, tasks_done_std=1.8, hours_logged_mean=8.2,
        meetings_mean=4.0, focus_mean=0.40, switches_mean=4.0, working_hours_mean=9.0,
        sleep_h_mean=6.3, stress_mean=0.62, steps_mean=5800,
        perf_score_mean=3.0, perf_score_std=0.7, promotion_probability_per_year=0.05,
        termination_probability_36m=0.18,
        peer_sentiment_mean=0.05, peer_volume_per_month=1.2,
        cooperation_360_mean=0.55, course_complete_rate=0.42, edge_density=0.45,
        burnout_prone=True,
    ),
    Archetype(
        name="star_perfectionist", share=10,
        tasks_done_mean=8.0, tasks_done_std=1.5, hours_logged_mean=9.5,
        meetings_mean=3.5, focus_mean=0.78, switches_mean=1.6, working_hours_mean=9.0,
        sleep_h_mean=7.0, stress_mean=0.55, steps_mean=8000,
        perf_score_mean=4.5, perf_score_std=0.4, promotion_probability_per_year=0.30,
        termination_probability_36m=0.06,
        peer_sentiment_mean=0.40, peer_volume_per_month=3.5,
        cooperation_360_mean=0.72, course_complete_rate=0.92, edge_density=0.7,
        leader=False,
    ),
    Archetype(
        name="quiet_rear_guard", share=15,
        tasks_done_mean=5.5, tasks_done_std=1.2, hours_logged_mean=8.0,
        meetings_mean=2.0, focus_mean=0.65, switches_mean=2.0, working_hours_mean=8.0,
        sleep_h_mean=7.5, stress_mean=0.30, steps_mean=7000,
        perf_score_mean=3.6, perf_score_std=0.4, promotion_probability_per_year=0.06,
        termination_probability_36m=0.04,
        peer_sentiment_mean=0.30, peer_volume_per_month=1.5,
        cooperation_360_mean=0.75, course_complete_rate=0.55, edge_density=0.55,
    ),
    Archetype(
        name="drifting_veteran", share=10,
        tasks_done_mean=3.5, tasks_done_std=1.5, hours_logged_mean=7.5,
        meetings_mean=3.0, focus_mean=0.45, switches_mean=3.5, working_hours_mean=7.5,
        sleep_h_mean=6.8, stress_mean=0.50, steps_mean=6500,
        perf_score_mean=2.8, perf_score_std=0.6, promotion_probability_per_year=0.02,
        termination_probability_36m=0.22,
        peer_sentiment_mean=-0.05, peer_volume_per_month=1.0,
        cooperation_360_mean=0.50, course_complete_rate=0.25, edge_density=0.40,
    ),
    Archetype(
        name="toxic_high_performer", share=5,
        tasks_done_mean=7.5, tasks_done_std=1.5, hours_logged_mean=9.0,
        meetings_mean=4.0, focus_mean=0.70, switches_mean=2.2, working_hours_mean=9.5,
        sleep_h_mean=6.5, stress_mean=0.65, steps_mean=7000,
        perf_score_mean=4.2, perf_score_std=0.5, promotion_probability_per_year=0.12,
        termination_probability_36m=0.10,
        peer_sentiment_mean=-0.30, peer_volume_per_month=4.5,
        cooperation_360_mean=0.30, course_complete_rate=0.65, edge_density=0.55,
        toxic=True,
    ),
    Archetype(
        name="isolated_newbie", share=10,
        tasks_done_mean=4.0, tasks_done_std=1.5, hours_logged_mean=7.5,
        meetings_mean=1.0, focus_mean=0.50, switches_mean=2.5, working_hours_mean=7.5,
        sleep_h_mean=6.8, stress_mean=0.55, steps_mean=6000,
        perf_score_mean=2.9, perf_score_std=0.6, promotion_probability_per_year=0.08,
        termination_probability_36m=0.20,
        peer_sentiment_mean=0.0, peer_volume_per_month=0.5,
        cooperation_360_mean=0.55, course_complete_rate=0.50, edge_density=0.20,
        isolated=True,
    ),
    Archetype(
        name="overwhelmed_manager", share=10,
        tasks_done_mean=3.0, tasks_done_std=1.0, hours_logged_mean=10.5,
        meetings_mean=8.5, focus_mean=0.30, switches_mean=5.5, working_hours_mean=11.0,
        sleep_h_mean=5.8, stress_mean=0.78, steps_mean=4500,
        perf_score_mean=3.7, perf_score_std=0.5, promotion_probability_per_year=0.10,
        termination_probability_36m=0.16,
        peer_sentiment_mean=0.20, peer_volume_per_month=3.0,
        cooperation_360_mean=0.65, course_complete_rate=0.40, edge_density=0.85,
        overworked=True, leader=True,
    ),
]


def by_name(name: str) -> Archetype:
    for a in ARCHETYPES:
        if a.name == name:
            return a
    raise KeyError(name)


def total_share() -> int:
    return sum(a.share for a in ARCHETYPES)


assert total_share() == 100, "Archetype shares must sum to 100"
