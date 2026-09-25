"""
CTD surface-soak detection and station classification (numpy-only).

Used by sfer_ctd_remove_surface_soak.py, comprehensive_analysis.py, and validation scripts.
"""

from __future__ import annotations

import warnings

import numpy as np

warnings.filterwarnings("ignore")

__all__ = [
    "SHALLOW_STABLE_KEEP_LAST_N",
    "PRE_SOAK_DEPTH_THRESHOLD_M",
    "classify_station",
    "detect_descent_start_v4",
    "detect_shallow_descent",
    "first_global_deepest_exclusive_end",
    "first_index_depth_at_least",
    "get_shallow_stable_keep_start_index",
    "get_soak_removal_index",
]

# For shallow_stable stations: keep only the last N scans (discard everything before).
SHALLOW_STABLE_KEEP_LAST_N = 250

# Before soak algorithms run, discard all scans before depth first reaches this (m, positive down).
PRE_SOAK_DEPTH_THRESHOLD_M = 2.0


def first_global_deepest_exclusive_end(depth_data: np.ndarray) -> int:
    """
    Exclusive end index: one past the first scan where depth equals the global maximum.

    Matches ``np.argmax`` on finite depths (first occurrence on a plateau). If the
    global maximum lies entirely before the soak start cut, callers should fall back
    to keeping through the end of the profile.

    Returns:
        Integer in ``[1, n]`` when there is at least one finite depth, else ``len(depth_data)``.
    """
    n = int(len(depth_data))
    if n == 0:
        return 0
    valid = np.isfinite(depth_data)
    if not np.any(valid):
        return n
    idx_all = np.flatnonzero(valid)
    y_all = depth_data[idx_all]
    i_max = int(np.argmax(y_all))
    return int(idx_all[i_max]) + 1


def first_index_depth_at_least(depth_data: np.ndarray, depth_m: float) -> int:
    """
    First scan index i where depth_data[i] >= depth_m.

    If depth never reaches depth_m (e.g. very shallow profile), returns 0 (no pre-cut).
    """
    if depth_data.size == 0:
        return 0
    hit = np.isfinite(depth_data) & (depth_data >= depth_m)
    if not np.any(hit):
        return 0
    return int(np.argmax(hit))


def get_shallow_stable_keep_start_index(total_scans: int) -> int:
    """
    For shallow_stable stations, return the scan index from which to keep data.
    We keep only the last SHALLOW_STABLE_KEEP_LAST_N points (default 500).
    Data before this index is considered surface soak and discarded.

    Returns:
        Start index (inclusive). Keep scans [start_idx, total_scans).
        If total_scans <= SHALLOW_STABLE_KEEP_LAST_N, returns 0 (keep all).
    """
    return max(0, total_scans - SHALLOW_STABLE_KEEP_LAST_N)


def detect_shallow_descent(depth_data: np.ndarray, verbose: bool = False) -> dict:
    """
    Specialized descent detection for shallow_stable stations.
    
    These stations have small depth ranges (< 3m) and subtle descents that
    the main V4 algorithm may miss. This algorithm uses:
    
    1. Compare initial vs final mean depth
    2. Look for transition point using rolling window comparison
    3. Use lower thresholds since descents are subtle
    
    If detection fails, caller should fall back to keeping last 500 scans.
    
    Returns:
        dict with 'detected', 'descent_start_idx', 'method', 'reason', etc.
    """
    result = {
        "detected": False,
        "method": None,
        "descent_start_idx": None,
    }
    
    n = len(depth_data)
    if n < 200:
        result["reason"] = "too_short"
        return result
    
    # Calculate baseline statistics
    initial_segment = depth_data[:min(200, n // 4)]
    initial_mean = np.mean(initial_segment)
    initial_std = np.std(initial_segment)
    
    final_segment = depth_data[-min(200, n // 4):]
    final_mean = np.mean(final_segment)
    
    depth_increase = final_mean - initial_mean
    depth_range = np.max(depth_data) - np.min(depth_data)
    
    result["initial_mean"] = initial_mean
    result["initial_std"] = initial_std
    result["final_mean"] = final_mean
    result["depth_increase"] = depth_increase
    result["depth_range"] = depth_range
    
    if verbose:
        print(f"  Initial mean: {initial_mean:.3f}m, std: {initial_std:.4f}m")
        print(f"  Final mean: {final_mean:.3f}m")
        print(f"  Depth increase (final - initial): {depth_increase:.3f}m")
    
    # Check if there's any descent at all
    # For shallow stations, even 8cm is significant, but must be above noise
    min_increase = max(3 * initial_std, 0.08)
    
    if depth_increase < min_increase:
        result["reason"] = "no_significant_increase"
        return result
    
    # ============================================================
    # METHOD 1: Rolling window comparison
    # ============================================================
    # Look for where depth consistently starts increasing
    
    window = min(100, n // 10)  # Adaptive window size
    step = max(10, window // 5)
    
    means = []
    positions = []
    for i in range(0, n - window, step):
        means.append(np.mean(depth_data[i:i + window]))
        positions.append(i + window // 2)
    
    means = np.array(means)
    positions = np.array(positions)
    
    noise_threshold = max(2 * initial_std, 0.05)  # At least 5cm
    
    transition_idx = None
    for i in range(len(means) - 3):
        current = means[i]
        # Check if we've departed from initial level
        if current > initial_mean + noise_threshold:
            # Check if it continues (sustained)
            if i + 3 < len(means):
                future_mean = np.mean(means[i:i+4])
                if future_mean > current - 0.02:  # Allow small dips
                    if means[min(i+3, len(means)-1)] > means[i] - 0.02:
                        transition_idx = positions[i] - window // 2
                        break
    
    # ============================================================
    # METHOD 2: Gradient-based with low threshold (fallback)
    # ============================================================
    if transition_idx is None:
        window_size = 200
        gradient_threshold = 0.0005  # 0.1m per 200 scans
        
        for i in range(0, n - window_size, 50):
            win = depth_data[i:i + window_size]
            gradient = (np.mean(win[-50:]) - np.mean(win[:50])) / 150
            
            if gradient > gradient_threshold:
                if i + window_size * 2 < n:
                    next_win = depth_data[i + window_size:i + window_size * 2]
                    next_gradient = (np.mean(next_win[-50:]) - np.mean(next_win[:50])) / 150
                    
                    if next_gradient > gradient_threshold * 0.3:
                        transition_idx = i
                        result["method"] = "gradient_shallow"
                        break
    
    # ============================================================
    # METHOD 3: Find point where depth exceeds threshold (fallback)
    # ============================================================
    if transition_idx is None:
        threshold = initial_mean + max(2 * initial_std, 0.06)
        
        for i in range(100, n - 100, 5):
            if depth_data[i] > threshold:
                next_50 = depth_data[i:i+50]
                if np.mean(next_50) > threshold and np.min(next_50) > initial_mean - 0.05:
                    transition_idx = i
                    result["method"] = "threshold_shallow"
                    break
    
    if transition_idx is not None:
        if result["method"] is None:
            result["method"] = "rolling_mean"
        
        # Refine: look backwards to find earliest departure
        fine_threshold = initial_mean + max(initial_std, 0.03)
        refine_start = max(0, transition_idx - 200)
        
        for j in range(refine_start, transition_idx + 10):
            if depth_data[j] > fine_threshold:
                if j + 30 < n:
                    check = depth_data[j:j+30]
                    if np.mean(check) > fine_threshold:
                        transition_idx = j
                        break
        
        result["detected"] = True
        result["descent_start_idx"] = max(0, transition_idx)
        
        if verbose:
            print(f"  Detected at scan {transition_idx} using {result['method']}")
    else:
        result["reason"] = "no_transition_found"
    
    return result


def _soak_removal_index_core(
    depth_data: np.ndarray, station_type: str, verbose: bool = False
) -> dict:
    """
    Soak removal on a single depth segment (no 2 m pre-trim).

    ``keep_from_idx`` is relative to the start of ``depth_data``.
    """
    n = len(depth_data)
    result: dict = {
        "keep_from_idx": 0,
        "method": None,
        "detected": False,
    }

    if station_type == "shallow_stable":
        detection = detect_shallow_descent(depth_data, verbose=verbose)

        if detection["detected"]:
            descent_start = detection["descent_start_idx"]
            leftover_count = n - descent_start
            if leftover_count < SHALLOW_STABLE_KEEP_LAST_N:
                result["keep_from_idx"] = get_shallow_stable_keep_start_index(n)
                result["method"] = "fallback_keep_last_500"
                result["detected"] = False
                result["fallback_reason"] = f"descent_detected_but_leftover_{leftover_count}_lt_500"
            else:
                result["keep_from_idx"] = descent_start
                result["method"] = f"shallow_descent_{detection['method']}"
                result["detected"] = True
        else:
            result["keep_from_idx"] = get_shallow_stable_keep_start_index(n)
            result["method"] = "fallback_keep_last_500"
            result["detected"] = False
            result["fallback_reason"] = detection.get("reason", "unknown")

    else:
        detection = detect_descent_start_v4(depth_data, verbose=verbose)

        if detection["detected"]:
            result["keep_from_idx"] = detection["descent_start_idx"]
            result["method"] = f"v4_{detection['method']}"
            result["detected"] = True
        else:
            shallow_detection = detect_shallow_descent(depth_data, verbose=verbose)
            if shallow_detection["detected"]:
                result["keep_from_idx"] = shallow_detection["descent_start_idx"]
                result["method"] = f"shallow_descent_{shallow_detection['method']}"
                result["detected"] = True
            else:
                result["keep_from_idx"] = get_shallow_stable_keep_start_index(n)
                result["method"] = "fallback_keep_last_500"
                result["detected"] = False

    return result


def get_soak_removal_index(
    depth_data: np.ndarray,
    station_type: str | None = None,
    verbose: bool = False,
) -> dict:
    """
    Scan index (in original profile coordinates) from which to keep data.

    Pipeline:
        1. Drop all scans before the first point where depth >= ``PRE_SOAK_DEPTH_THRESHOLD_M``
           (2 m by default). If the profile never reaches that depth, no cut.
        2. Classify station on the remaining segment (unless ``station_type`` is passed).
        3. Run soak removal (_soak_removal_index_core) on that segment.
        4. Map the result back to indices in the original ``depth_data``.

    Args:
        depth_data: Full depth series (positive downward).
        station_type: Optional ``shallow_stable`` / ``shallow_cast`` / ``deep_cast``.
            If None, computed with ``classify_station`` on the post-2 m segment.
        verbose: Print debug info.

    Returns:
        dict including:
            ``keep_from_idx`` — index in original array to start keeping
            ``keep_until_idx`` — exclusive end index; keep scans ``[keep_from_idx, keep_until_idx)``.
                Set to the scan after the **first** global maximum depth (finite values only).
                If that deepest point lies at or before ``keep_from_idx``, falls back to ``n_full``.
            ``pre_depth_trim_idx`` — first index at or above 2 m (0 if no pre-trim)
            ``post_pretrim_keep_from_idx`` — soak cut relative to post-trim segment
            ``station_type`` — type used for soak logic
            ``method``, ``detected``, optional ``fallback_reason``
    """
    n_full = int(len(depth_data))
    pre_idx = first_index_depth_at_least(depth_data, PRE_SOAK_DEPTH_THRESHOLD_M)
    trimmed = depth_data[pre_idx:]

    if station_type is None:
        stype = classify_station(trimmed)
    else:
        stype = station_type

    core = _soak_removal_index_core(trimmed, stype, verbose=verbose)
    keep_inner = int(core["keep_from_idx"])
    keep_from_full = pre_idx + keep_inner
    keep_from_full = max(0, min(keep_from_full, n_full))

    end_exc = first_global_deepest_exclusive_end(depth_data)
    if end_exc <= keep_from_full:
        keep_until_full = n_full
    else:
        keep_until_full = min(end_exc, n_full)
    keep_until_full = max(keep_until_full, min(keep_from_full + 1, n_full))

    out = {
        **core,
        "keep_from_idx": keep_from_full,
        "keep_until_idx": keep_until_full,
        "pre_depth_trim_idx": pre_idx,
        "post_pretrim_keep_from_idx": keep_inner,
        "station_type": stype,
    }
    if pre_idx > 0:
        cm = core.get("method") or "unknown"
        out["method"] = f"pretrim_{PRE_SOAK_DEPTH_THRESHOLD_M:g}m_then_{cm}"
    return out


def detect_descent_start_v4(depth_data: np.ndarray, verbose: bool = False) -> dict:
    """
    Improved descent detection algorithm based on comprehensive analysis.
    
    PRIMARY METHOD: Depth departure from initial level
    - This is more robust than gradient detection
    - Not fooled by small-scale noise
    
    SECONDARY METHOD: Large-window gradient detection
    - Uses 200-scan windows to filter out noise
    - Only as fallback
    
    KEY INSIGHT: The descent is defined by when depth first EXCEEDS the
    initial surface level by a threshold, not by gradient alone.
    """
    result = {
        "detected": False,
        "method": None,
        "all_candidates": []
    }
    
    if len(depth_data) < 200:
        result["reason"] = "too_short"
        return result
    
    # Calculate initial baseline statistics
    # Use first 200 scans for stable baseline
    initial_segment = depth_data[:min(200, len(depth_data) // 4)]
    initial_mean = np.mean(initial_segment)
    initial_std = np.std(initial_segment)
    initial_min = np.min(initial_segment)
    
    # Final depth statistics
    final_segment = depth_data[-min(100, len(depth_data) // 4):]
    final_mean = np.mean(final_segment)
    max_depth = np.max(depth_data)
    
    # Check if there's actually a significant descent
    depth_range = max_depth - np.min(depth_data)
    if final_mean - initial_mean < 1.0 and depth_range < 2.0:
        result["reason"] = "no_significant_descent"
        result["depth_range"] = depth_range
        return result
    
    if verbose:
        print(f"  Initial: mean={initial_mean:.2f}, std={initial_std:.3f}")
        print(f"  Final: mean={final_mean:.2f}, max={max_depth:.2f}")
        print(f"  Depth range: {depth_range:.2f}")
    
    # ============================================================
    # PRIMARY METHOD: Depth departure detection
    # ============================================================
    # Find first point where depth consistently exceeds initial level
    # Use adaptive threshold based on initial noise level
    
    # The threshold should be higher than normal noise but sensitive to real descent
    # Real descent: depth increases by 0.2-0.5m within 50-100 scans
    # Noise: random fluctuations around mean, typically < 2*std
    
    noise_level = max(initial_std * 2, 0.15)  # At least 15cm above noise
    departure_threshold = initial_mean + max(noise_level + 0.1, 0.25)  # At least 25cm above mean
    
    if verbose:
        print(f"  Noise level: {noise_level:.3f}, Departure threshold: {departure_threshold:.3f}")
    
    # Scan for first consistent departure
    descent_candidates = []
    
    for i in range(50, len(depth_data) - 100, 5):  # Check every 5 scans
        if depth_data[i] > departure_threshold:
            # Verify it's not just noise - check next 100 scans
            next_100 = depth_data[i:i+100]
            mean_next = np.mean(next_100)
            min_next = np.min(next_100)
            
            # Must meet criteria:
            # 1. Mean of next 100 > threshold
            # 2. Min of next 100 > initial_mean (no return to surface)
            # 3. Depth continues to increase (positive trend)
            if (mean_next > departure_threshold and 
                min_next > initial_mean - 0.1):
                
                # Check trend continues
                if i + 200 < len(depth_data):
                    next_200 = depth_data[i:i+200]
                    trend = (np.mean(next_200[-50:]) - np.mean(next_200[:50])) / 150
                    if trend > 0.001:  # Positive trend
                        # Found valid descent start - now refine the exact point
                        # Look backwards to find the very start
                        refine_start = max(0, i - 100)
                        fine_threshold = initial_mean + noise_level * 0.5
                        
                        for j in range(refine_start, i + 10):
                            if depth_data[j] > fine_threshold:
                                # Verify this point
                                if j + 30 < len(depth_data):
                                    check_window = depth_data[j:j+30]
                                    if np.mean(check_window) > fine_threshold:
                                        descent_candidates.append({
                                            "method": "departure",
                                            "idx": j,
                                            "confidence": 5,
                                            "depth_at_detection": depth_data[j],
                                            "threshold_used": fine_threshold
                                        })
                                        break
                        break
    
    # ============================================================
    # SECONDARY METHOD: Large-window gradient detection
    # ============================================================
    # Only use 200-scan windows - smaller windows detect noise
    
    window_size = 200
    gradient_threshold = 0.003  # 0.3m per 100 scans = clear descent
    step = 50
    
    for i in range(0, len(depth_data) - window_size * 2, step):
        window = depth_data[i:i + window_size]
        gradient = (window[-1] - window[0]) / window_size
        
        if gradient > gradient_threshold:
            # Verify with next window
            next_window = depth_data[i + window_size:i + window_size * 2]
            next_gradient = (next_window[-1] - next_window[0]) / window_size
            
            if next_gradient > gradient_threshold * 0.5:  # Continues descending
                # Refine start point
                for j in range(max(0, i - 50), i + 50):
                    if j + 50 < len(depth_data):
                        small_win = depth_data[j:j + 50]
                        small_grad = (small_win[-1] - small_win[0]) / 50
                        if small_grad > 0.002:
                            descent_candidates.append({
                                "method": "gradient_200",
                                "idx": j,
                                "confidence": 3,
                                "gradient": gradient
                            })
                            break
                break
    
    # ============================================================
    # Choose best candidate
    # ============================================================
    if descent_candidates:
        # Prefer departure method (more reliable)
        departure_cands = [c for c in descent_candidates if c["method"] == "departure"]
        if departure_cands:
            best = departure_cands[0]
        else:
            # Use earliest gradient detection
            gradient_cands = sorted([c for c in descent_candidates if "gradient" in c["method"]], 
                                   key=lambda x: x["idx"])
            best = gradient_cands[0] if gradient_cands else descent_candidates[0]
        
        result["detected"] = True
        result["descent_start_idx"] = best["idx"]
        result["method"] = best["method"]
        result["confidence"] = best.get("confidence", 1)
        result["all_candidates"] = descent_candidates
        
        if verbose:
            print(f"  Detected at scan {best['idx']} using {best['method']}")
    else:
        result["reason"] = "no_consistent_descent_pattern"
    
    return result


def classify_station(depth_data: np.ndarray) -> str:
    """
    Classify station type based on comprehensive analysis.
    
    NEW LOGIC:
    - depth_range < 1.5m: shallow_stable (no detection needed; keep only last 500 scans)
    - 1.5m <= depth_range < 3m: check if descent detected
      - If yes: treat as shallow_cast
      - If no: treat as shallow_stable
    - depth_range >= 3m: shallow_cast or deep_cast (use detection algorithm)
    """
    depth_range = np.max(depth_data) - np.min(depth_data)
    
    if depth_range < 1.5:
        return "shallow_stable"  # Definitely no cast
    elif depth_range < 3.0:
        # Transition zone - check for actual descent
        detection = detect_descent_start_v4(depth_data)
        if detection["detected"]:
            return "shallow_cast"  # Has descent, treat as cast
        else:
            return "shallow_stable"  # No descent detected
    elif depth_range < 10.0:
        return "shallow_cast"
    else:
        return "deep_cast"
