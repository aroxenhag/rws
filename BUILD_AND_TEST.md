# RWS Build and Test Instructions

## Docker Environment

**Container:** `emoco-devbox`
**User:** `emoco`
**Project Path:** `/workspace/rws`
**ROS2 Distro:** Jazzy

## Building the Project

```bash
docker exec -u emoco emoco-devbox bash -c "source /opt/ros/jazzy/setup.bash && cd /workspace/rws && colcon build"
```

## Running Tests

```bash
# Run all tests
docker exec -u emoco emoco-devbox bash -c "source /opt/ros/jazzy/setup.bash && cd /workspace/rws && colcon test --packages-select rws --event-handlers console_direct+"

# Run specific test
docker exec -u emoco emoco-devbox bash -c "source /opt/ros/jazzy/setup.bash && cd /workspace/rws/build/rws && ctest -R client_handler_test --output-on-failure"

# Rerun failed tests with output
docker exec -u emoco emoco-devbox bash -c "source /opt/ros/jazzy/setup.bash && cd /workspace/rws/build/rws && ctest --rerun-failed --output-on-failure"
```

## Running the Server

```bash
# Basic run
docker exec -u emoco emoco-devbox bash -c "source /opt/ros/jazzy/setup.bash && cd /workspace/rws && source install/setup.bash && RMW_IMPLEMENTATION=rmw_fastrtps_dynamic_cpp ros2 launch rws rws_server_launch.py"

# With timing diagnostics enabled
docker exec -u emoco emoco-devbox bash -c "source /opt/ros/jazzy/setup.bash && cd /workspace/rws && source install/setup.bash && RMW_IMPLEMENTATION=rmw_fastrtps_dynamic_cpp ros2 launch rws rws_server_launch.py enable_timing_logs:=True"

# With timing logs to file (for visualization)
docker exec -u emoco emoco-devbox bash -c "source /opt/ros/jazzy/setup.bash && cd /workspace/rws && source install/setup.bash && RMW_IMPLEMENTATION=rmw_fastrtps_dynamic_cpp ros2 launch rws rws_server_launch.py enable_timing_logs:=True timing_log_file:=/tmp/rws_timing.log"
```

## Timing Visualization

Analyze where time is spent in the bridge using ASCII charts. See [scripts/README_TIMING.md](scripts/README_TIMING.md) for full documentation.

### Quick Start - Live Monitoring (Recommended) ⭐

```bash
# Enable timing logs to file
ros2 launch rws rws_server_launch.py enable_timing_logs:=True timing_log_file:=/tmp/rws_timing.log

# Start interactive live monitor (auto-updates, switchable views)
python3 scripts/rws_timing_live.py /tmp/rws_timing.log

# Controls: [1-5] switch views, [w] time window, [r] reset, [q] quit
```

### Static Analysis (One-time Reports)

```bash
# Generate one-time report (bar chart)
python3 scripts/rws_timing_visualizer.py --file /tmp/rws_timing.log

# Other options
python3 scripts/rws_timing_visualizer.py --file /tmp/rws_timing.log --chart-type pie
python3 scripts/rws_timing_visualizer.py --file /tmp/rws_timing.log --by-service
python3 scripts/rws_timing_visualizer.py --file /tmp/rws_timing.log --percentiles
python3 scripts/rws_timing_visualizer.py --file /tmp/rws_timing.log --minutes 10
```

### Example Output

```
Average Time per Phase (Dispatch)
================================================================
Serialize        ████████████████████████████  233.1μs (52.4%)
Ready Check      ████████████████████          168.9μs (38.0%)
Cache Check      ██                             18.1μs ( 4.1%)
Send Request     █                              12.3μs ( 2.8%)
Client Setup     █                              12.1μs ( 2.7%)

Average Total Dispatch Time: 444.6μs (0.44ms)
Service Calls Analyzed: 15
```

## Verifying Build

```bash
# Check if binary exists
docker exec -u emoco emoco-devbox bash -c "ls -lh /workspace/rws/install/rws/lib/rws/rws_server"

# Check if package is installed
docker exec -u emoco emoco-devbox bash -c "source /opt/ros/jazzy/setup.bash && cd /workspace/rws && source install/setup.bash && ros2 pkg list | grep rws"

# Verify optimizations are compiled in
docker exec -u emoco emoco-devbox bash -c "nm /workspace/rws/install/rws/lib/rws/rws_server | grep -E 'update_service_cache|is_service_available'"
```

## Clean Build

```bash
docker exec -u emoco emoco-devbox bash -c "cd /workspace/rws && rm -rf build install log && source /opt/ros/jazzy/setup.bash && colcon build"
```

## Test Summary

### Passing Tests (7/8)
- ✅ **async_behavior_test** - NEW! Tests that service calls don't block topic messages
- ✅ **client_handler_test** - Tests service and topic handling
- ✅ **connector_test** - Tests connector functionality
- ✅ **typesupport_helpers_test** - Tests type support helpers
- ✅ **lint_cmake** - CMake linting
- ✅ **pep257** - Python docstring linting
- ✅ **xmllint** - XML validation

### Known Failures (1/8)
- ❌ **rws_translate_test** - Fails due to missing `test_msgs` package (pre-existing, unrelated to async fixes)

## Recent Optimizations (2024-10-01)

### Problem Solved
Service requests were handled serially, causing lag when slow services blocked the processing queue. Topic messages and fast service responses had to wait for slow service calls to complete.

### Solution Implemented
1. **Removed thread pool** - It was blocking worker threads with `wait_for_service()` loops
2. **Pure async pattern** - All service calls use `async_send_request()` with callbacks
3. **Service lookup caching** - Cache refreshes every 1s instead of querying on every call
4. **Fast pre-checks** - Non-blocking `service_is_ready()` check before dispatch
5. **Fixed cache initialization** - Cache now populates on first use (was broken)

### Key Files Modified
- `include/rws/client_handler.hpp` - Removed thread pool, added service cache
- `src/client_handler.cpp` - Replaced blocking calls with pure async pattern
- `launch/rws_server_launch.py` - Added `enable_timing_logs` parameter

### Verification
- Main thread never blocks on service calls
- Topic messages flow independently of service calls
- Multiple service calls execute concurrently via ROS2 async callbacks
- Service lookups use cached data (microseconds vs milliseconds)

### New Test: async_behavior_test
A comprehensive test suite that verifies the async behavior fixes:

1. **AsyncCallsReturnImmediately** - Verifies async calls return without blocking
2. **MultipleCallsExecuteConcurrently** - Verifies multiple calls execute in parallel
3. **FastOperationsNotBlockedBySlowOnes** - Key test: fast responses don't wait for slow operations
4. **MainThreadRemainsResponsiveDuringAsyncOps** - Verifies main thread can process messages while services are pending
5. **CachedLookupsAreFast** - Verifies service cache improves performance

Run with:
```bash
docker exec -u emoco emoco-devbox bash -c "source /opt/ros/jazzy/setup.bash && cd /workspace/rws/build/rws && ctest -R async_behavior_test --output-on-failure"
```
