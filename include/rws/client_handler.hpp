// Copyright 2022 Vasily Kiniv
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

#ifndef RWS__NODE_HPP_
#define RWS__NODE_HPP_

#include <nlohmann/json.hpp>
#include <mutex>
#include <chrono>

#include "rclcpp/rclcpp.hpp"
#include "rws/connector.hpp"
#include "rws/generic_client.hpp"

namespace rws
{

using json = nlohmann::json;
class ClientHandler
{
public:
  ClientHandler(
    int client_id, std::shared_ptr<rws::NodeInterface<>> node,
    std::shared_ptr<Connector<>> connector, bool rosbridge_compatible,
    std::function<void(std::string & msg)> callback,
    std::function<void(std::vector<std::uint8_t> & msg)> binary_callback);
  json process_message(json & msg);
  void set_timing_logs_enabled(bool enabled) { enable_timing_logs_ = enabled; }
  void set_timing_log_file(const std::string& file) { timing_log_file_ = file; }

  ~ClientHandler();

private:
  int client_id_;
  std::shared_ptr<rws::NodeInterface<>> node_;
  std::shared_ptr<rws::Connector<>> connector_;
  bool rosbridge_compatible_;
  std::function<void(std::string & msg)> callback_;
  std::function<void(std::vector<std::uint8_t> & msg)> binary_callback_;

  std::map<std::string, std::function<void()>> subscriptions_;
  std::map<std::string, std::function<void()>> publishers_;
  std::map<std::string, std::string> publisher_type_;
  std::map<std::string, std::function<void(std::shared_ptr<const rclcpp::SerializedMessage>)>>
    publisher_cb_;
  std::map<std::string, std::shared_ptr<rws::GenericClient>> clients_;
  std::mutex clients_mutex_;

  // Service caching for performance
  std::map<std::string, std::vector<std::string>> service_cache_;
  std::chrono::steady_clock::time_point service_cache_time_;
  std::mutex service_cache_mutex_;
  static constexpr int SERVICE_CACHE_MS = 1000; // Cache service list for 1 second

  bool enable_timing_logs_;
  std::string timing_log_file_;
  std::mutex timing_log_mutex_;

  bool is_service_available(const std::string& service_name);
  void update_service_cache();
  void log_timing(const std::string& message);

  rclcpp::Logger get_logger()
  {
    return rclcpp::get_logger(std::string("client_handler_") + std::to_string(client_id_));
  }

  void send_message(std::string & msg);
  void send_message(std::vector<std::uint8_t> & msg);

  // Topic handlers
  bool subscribe_to_topic(const json & request, json & response_out);
  bool unsubscribe_from_topic(const json & request, json & response_out);
  bool advertise_topic(const json & request, json & response_out);
  bool unadvertise_topic(const json & request, json & response_out);
  bool publish_to_topic(const json & request, json & response_out);
  void subscription_callback(topic_params & params, std::shared_ptr<const rclcpp::SerializedMessage> message);

  // Service handlers
  bool call_service(const json & request, json & response_out);
  bool call_external_service(const json & request, json & response_out);
};

}  // namespace rws

#endif  // RWS__NODE_HPP_