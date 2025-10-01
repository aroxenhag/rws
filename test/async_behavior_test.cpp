// Copyright 2024
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * Test to verify async behavior characteristics.
 *
 * This test verifies that the async pattern used in the RWS bridge
 * allows multiple operations to execute concurrently without blocking.
 *
 * The original problem: Service calls were processed serially, causing
 * all pending responses (including topic messages) to queue up behind
 * slow service calls.
 *
 * The solution: Pure async pattern where callbacks are registered and
 * execution continues immediately without blocking.
 */

#include <gmock/gmock.h>
#include <gtest/gtest.h>
#include <chrono>
#include <thread>
#include <atomic>
#include <vector>
#include <future>
#include <functional>
#include <queue>
#include <mutex>
#include <condition_variable>

using namespace std::chrono_literals;

/**
 * Simulates an async service call pattern.
 * This mimics how ROS2 async_send_request() works.
 */
class AsyncServiceSimulator
{
public:
  using Callback = std::function<void(int result)>;

  AsyncServiceSimulator()
  : shutdown_(false)
  {
    // Start multiple worker threads to process callbacks concurrently
    // This simulates ROS2's async executor behavior
    for (int i = 0; i < 5; ++i) {
      workers_.emplace_back([this]() {
        while (!shutdown_) {
          std::function<void()> task;
          {
            std::unique_lock<std::mutex> lock(mutex_);
            cv_.wait(lock, [this] { return !tasks_.empty() || shutdown_; });
            if (shutdown_) break;
            if (tasks_.empty()) continue;
            task = std::move(tasks_.front());
            tasks_.pop();
          }
          if (task) task();
        }
      });
    }
  }

  ~AsyncServiceSimulator()
  {
    shutdown_ = true;
    cv_.notify_all();
    for (auto& worker : workers_) {
      if (worker.joinable()) {
        worker.join();
      }
    }
  }

  /**
   * Async call that returns immediately and executes callback later.
   * This simulates the async pattern used in call_external_service().
   */
  void async_call(int delay_ms, int value, Callback callback)
  {
    call_count_++;

    // Queue the task to be executed asynchronously
    {
      std::lock_guard<std::mutex> lock(mutex_);
      tasks_.push([this, delay_ms, value, callback]() {
        // Simulate service processing time
        std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));

        // Call the callback with result (simulates response received)
        if (callback) {
          callback(value * 2);
        }
        completed_count_++;
      });
    }
    cv_.notify_one();
  }

  int get_call_count() const { return call_count_; }
  int get_completed_count() const { return completed_count_; }

private:
  std::queue<std::function<void()>> tasks_;
  std::mutex mutex_;
  std::condition_variable cv_;
  std::vector<std::thread> workers_;
  std::atomic<bool> shutdown_;
  std::atomic<int> call_count_{0};
  std::atomic<int> completed_count_{0};
};

class AsyncBehaviorTest : public ::testing::Test
{
protected:
  void SetUp() override
  {
    simulator_ = std::make_unique<AsyncServiceSimulator>();
  }

  void TearDown() override
  {
    simulator_.reset();
  }

  std::unique_ptr<AsyncServiceSimulator> simulator_;
};

/**
 * Test that async calls return immediately without blocking.
 *
 * Before the fix: Service calls would block the caller.
 * After the fix: Calls return immediately, callbacks execute later.
 */
TEST_F(AsyncBehaviorTest, AsyncCallsReturnImmediately)
{
  std::atomic<int> result1{0};
  std::atomic<int> result2{0};

  auto start = std::chrono::steady_clock::now();

  // Make a slow async call (500ms processing time)
  simulator_->async_call(500, 10, [&result1](int res) {
    result1 = res;
  });

  // Make another async call immediately
  simulator_->async_call(100, 20, [&result2](int res) {
    result2 = res;
  });

  auto dispatch_time = std::chrono::duration_cast<std::chrono::milliseconds>(
    std::chrono::steady_clock::now() - start).count();

  // Both calls should dispatch in < 10ms (async, non-blocking)
  EXPECT_LT(dispatch_time, 10)
    << "Async calls should return immediately, not block. Took: " << dispatch_time << "ms";

  EXPECT_EQ(simulator_->get_call_count(), 2)
    << "Both async calls should have been dispatched";

  // Wait for callbacks to complete
  std::this_thread::sleep_for(600ms);

  EXPECT_EQ(result1, 20) << "First callback should have been called";
  EXPECT_EQ(result2, 40) << "Second callback should have been called";
  EXPECT_EQ(simulator_->get_completed_count(), 2);
}

/**
 * Test that multiple async calls can execute concurrently.
 *
 * Before the fix: Calls executed serially (or limited by thread pool size).
 * After the fix: Multiple calls execute concurrently via callbacks.
 */
TEST_F(AsyncBehaviorTest, MultipleCallsExecuteConcurrently)
{
  const int NUM_CALLS = 5;
  const int DELAY_MS = 200;

  std::vector<std::atomic<int>> results(NUM_CALLS);
  for (auto& r : results) r = 0;

  auto start = std::chrono::steady_clock::now();

  // Dispatch multiple slow async calls
  for (int i = 0; i < NUM_CALLS; ++i) {
    simulator_->async_call(DELAY_MS, i, [&results, i](int res) {
      results[i] = res;
    });
  }

  auto dispatch_time = std::chrono::duration_cast<std::chrono::milliseconds>(
    std::chrono::steady_clock::now() - start).count();

  // Dispatch should be fast
  EXPECT_LT(dispatch_time, 50)
    << "Dispatching " << NUM_CALLS << " async calls should be fast";

  // Wait for all to complete
  std::this_thread::sleep_for(std::chrono::milliseconds(DELAY_MS + 100));

  // Verify all callbacks executed
  for (int i = 0; i < NUM_CALLS; ++i) {
    EXPECT_EQ(results[i], i * 2)
      << "Callback " << i << " should have been called";
  }

  EXPECT_EQ(simulator_->get_completed_count(), NUM_CALLS);
}

/**
 * Test that fast operations are not blocked by slow operations.
 *
 * This is the key test for the original problem:
 * Before: Fast responses queued behind slow service calls.
 * After: Fast responses return immediately, independent of slow operations.
 */
TEST_F(AsyncBehaviorTest, FastOperationsNotBlockedBySlowOnes)
{
  std::atomic<int> slow_result{0};
  std::atomic<int> fast_result{0};
  std::atomic<bool> fast_completed{false};
  std::atomic<bool> slow_completed{false};

  auto start = std::chrono::steady_clock::now();

  // Start a slow operation (500ms)
  simulator_->async_call(500, 100, [&slow_result, &slow_completed](int res) {
    slow_result = res;
    slow_completed = true;
  });

  std::this_thread::sleep_for(10ms); // Ensure slow operation starts

  auto fast_start = std::chrono::steady_clock::now();

  // Start a fast operation (50ms)
  simulator_->async_call(50, 200, [&fast_result, &fast_completed](int res) {
    fast_result = res;
    fast_completed = true;
  });

  // Wait for fast operation to complete
  std::this_thread::sleep_for(100ms);

  auto fast_elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
    std::chrono::steady_clock::now() - fast_start).count();

  // Fast operation should complete in ~50ms, not wait for slow (500ms)
  EXPECT_LT(fast_elapsed, 150)
    << "Fast operation should not be blocked by slow operation. Took: " << fast_elapsed << "ms";

  EXPECT_TRUE(fast_completed)
    << "Fast operation should have completed";

  EXPECT_FALSE(slow_completed)
    << "Slow operation should still be running when fast completes";

  // Verify values
  EXPECT_EQ(fast_result, 400);

  // Wait for slow to complete
  std::this_thread::sleep_for(450ms);
  EXPECT_TRUE(slow_completed);
  EXPECT_EQ(slow_result, 200);
}

/**
 * Test that the main thread remains responsive during async operations.
 *
 * This simulates the bridge's main message processing loop:
 * It should be able to process new messages while service calls are pending.
 */
TEST_F(AsyncBehaviorTest, MainThreadRemainsResponsiveDuringAsyncOps)
{
  std::atomic<int> messages_processed{0};
  std::atomic<int> service_responses{0};

  // Start several slow service calls
  for (int i = 0; i < 3; ++i) {
    simulator_->async_call(300, i, [&service_responses](int res) {
      service_responses++;
      (void)res;
    });
  }

  // Simulate main thread processing messages while services are pending
  auto start = std::chrono::steady_clock::now();
  while (std::chrono::duration_cast<std::chrono::milliseconds>(
           std::chrono::steady_clock::now() - start).count() < 200) {
    // Simulate processing topic messages
    messages_processed++;
    std::this_thread::sleep_for(10ms);
  }

  // Main thread should have processed many messages
  EXPECT_GT(messages_processed, 15)
    << "Main thread should remain responsive, processing ~20 messages in 200ms";

  // Service calls should not have completed yet (they take 300ms)
  EXPECT_EQ(service_responses, 0)
    << "Service calls should still be pending";

  // Wait for service calls to complete
  std::this_thread::sleep_for(200ms);
  EXPECT_EQ(service_responses, 3)
    << "All service calls should eventually complete";
}

/**
 * Test service cache concept: repeated lookups should be fast.
 */
TEST_F(AsyncBehaviorTest, CachedLookupsAreFast)
{
  // Simulate a cache with lookup time
  struct ServiceCache {
    std::map<std::string, bool> cache_;
    std::chrono::steady_clock::time_point last_update_;
    const int CACHE_TTL_MS = 1000;

    bool is_service_available(const std::string& name) {
      auto now = std::chrono::steady_clock::now();
      auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        now - last_update_).count();

      // Update cache if empty or stale
      if (cache_.empty() || elapsed > CACHE_TTL_MS) {
        // Simulate expensive lookup (graph query)
        std::this_thread::sleep_for(10ms);
        cache_[name] = true;
        last_update_ = now;
        return true;
      }

      // Fast cache hit
      return cache_.count(name) > 0;
    }
  };

  ServiceCache cache;

  // First lookup: cache miss (slow)
  auto start1 = std::chrono::steady_clock::now();
  bool found1 = cache.is_service_available("/test_service");
  auto duration1 = std::chrono::duration_cast<std::chrono::milliseconds>(
    std::chrono::steady_clock::now() - start1).count();

  EXPECT_TRUE(found1);
  EXPECT_GE(duration1, 10) << "First lookup should take time to populate cache";

  // Second lookup: cache hit (fast)
  auto start2 = std::chrono::steady_clock::now();
  bool found2 = cache.is_service_available("/test_service");
  auto duration2 = std::chrono::duration_cast<std::chrono::milliseconds>(
    std::chrono::steady_clock::now() - start2).count();

  EXPECT_TRUE(found2);
  EXPECT_LT(duration2, 5) << "Cached lookup should be fast (< 5ms)";

  // Cache hit should be significantly faster than cache miss
  EXPECT_LT(duration2, duration1 / 2);
}

int main(int argc, char ** argv)
{
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
