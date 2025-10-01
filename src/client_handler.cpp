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

#include "rws/client_handler.hpp"

#include <chrono>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <nlohmann/json.hpp>

#include "rclcpp/logger.hpp"
#include "rclcpp/qos.hpp"
#include "rclcpp/rclcpp.hpp"
#include "rws/translate.hpp"

namespace rws
{

using json = nlohmann::json;
using namespace std::chrono_literals;
using std::placeholders::_1;

std::string string_thread_id()
{
  auto hashed = std::hash<std::thread::id>()(std::this_thread::get_id());
  return std::to_string(hashed);
}

ClientHandler::ClientHandler(
  int client_id, std::shared_ptr<rws::NodeInterface<>> node, std::shared_ptr<Connector<>> connector,
  bool rosbridge_compatible, std::function<void(std::string & msg)> callback,
  std::function<void(std::vector<std::uint8_t> & msg)> binary_callback)
: client_id_(client_id),
  node_(node),
  connector_(connector),
  rosbridge_compatible_(rosbridge_compatible),
  callback_(callback),
  binary_callback_(binary_callback),
  service_cache_time_(std::chrono::steady_clock::now()),
  enable_timing_logs_(false)
{
  RCLCPP_INFO(
    get_logger(), "Constructing client %s(%s)", std::to_string(client_id_).c_str(),
    string_thread_id().c_str());
}

ClientHandler::~ClientHandler()
{
  RCLCPP_INFO(
    get_logger(), "Destroying client %s(%s)", std::to_string(client_id_).c_str(),
    string_thread_id().c_str());

  for (auto it = subscriptions_.begin(); it != subscriptions_.end(); ++it) {
    it->second();
  }
  for (auto it = publishers_.begin(); it != publishers_.end(); ++it) {
    it->second();
  }
}

json ClientHandler::process_message(json & msg)
{
  bool handled = false;
  json response = {{"id", msg["id"]}, {"result", false}};

  if (!msg.contains("op")) {
    response["error"] = "No op specified";
    RCLCPP_ERROR(get_logger(), response["error"].dump().c_str());
    return response;
  }

  std::string op = msg["op"];

  if (op == "call_service") {
    handled = call_service(msg, response);
  }

  if (op == "subscribe") {
    handled = subscribe_to_topic(msg, response);
  }

  if (op == "advertise") {
    handled = advertise_topic(msg, response);
  }

  if (op == "unadvertise") {
    handled = unadvertise_topic(msg, response);
  }

  if (op == "publish") {
    handled = publish_to_topic(msg, response);
  }

  if (op == "unsubscribe") {
    handled = unsubscribe_from_topic(msg, response);
  }

  if (!handled) {
    RCLCPP_WARN(get_logger(), "Unhadled request: %s", msg.dump().c_str());
  }

  return response;
}

void ClientHandler::send_message(std::string & msg)
{
  if (this->callback_) {
    this->callback_(msg);
  }
}

void ClientHandler::send_message(std::vector<std::uint8_t> & msg)
{
  if (this->binary_callback_) {
    this->binary_callback_(msg);
  }
}

void ClientHandler::subscription_callback(topic_params & params, std::shared_ptr<const rclcpp::SerializedMessage> message)
{
  uint32_t secs = node_->now().seconds();
  uint32_t nsecs = node_->now().nanoseconds() - (secs * 1000000000);

  json m = {
    {"op", "publish"},
    {"topic", params.topic},
  };

  auto compression = params.compression;
  auto sub_type = params.type;

  if (compression == "cbor-raw") {
    auto buf = std::vector<std::uint8_t>(
      &message->get_rcl_serialized_message().buffer[0],
      &message->get_rcl_serialized_message()
          .buffer[message->get_rcl_serialized_message().buffer_length]);
    m["msg"] = {{"secs", secs}, {"nsecs", nsecs}, {"bytes", json::binary_t(buf)}};
    std::vector<std::uint8_t> cbor_buf = json::to_cbor(m);
    this->send_message(cbor_buf);
  } else if (compression == "cbor") {
    m["msg"] = rws::serialized_message_to_json(sub_type, std::move(message));
    std::vector<std::uint8_t> buf = json::to_cbor(m);
    this->send_message(buf);
  } else if (compression == "bson") {
    m["msg"] = rws::serialized_message_to_json(sub_type, message);
    std::vector<std::uint8_t> buf = json::to_bson(m);
    this->send_message(buf);
  } else if (compression == "msgpack") {
    m["msg"] = rws::serialized_message_to_json(sub_type, message);
    std::vector<std::uint8_t> buf = json::to_msgpack(m);
    this->send_message(buf);
  } else if (compression == "ubjson") {
    m["msg"] = rws::serialized_message_to_json(sub_type, message);
    std::vector<std::uint8_t> buf = json::to_ubjson(m);
    this->send_message(buf);
  } else if (compression == "bjdata") {
    m["msg"] = rws::serialized_message_to_json(sub_type, message);
    std::vector<std::uint8_t> buf = json::to_bjdata(m);
    this->send_message(buf);
  } else {
    m["msg"] = rws::serialized_message_to_json(sub_type, message);
    std::string json_str = m.dump();
    this->send_message(json_str);
  }
}

bool ClientHandler::subscribe_to_topic(const json & msg, json & response)
{
  response["op"] = "subscribe_response";
  if (!msg.contains("topic") || !msg["topic"].is_string()) {
    response["result"] = false;
    response["error"] = "No topic specified";
    RCLCPP_ERROR(get_logger(), response["error"].dump().c_str());
    return true;
  }

  std::string topic = msg["topic"];
  std::map<std::string, std::vector<std::string>> topics = node_->get_topic_names_and_types();
  if (topics.find(topic) == topics.end()) {
    response["error"] = "Topic " + topic + " not found";
    response["result"] = false;
    RCLCPP_ERROR(
      get_logger(), "Failed to subscribe to topic: %s", response["error"].dump().c_str());
    return true;
  }
  size_t history_depth = 10;
  if (msg.contains("history_depth") && msg["history_depth"].is_number()) {
    history_depth = msg["history_depth"];
  } else if (msg.contains("queue_size") && msg["queue_size"].is_number()) {
    history_depth = msg["queue_size"];
  }
  rclcpp::Duration throttle_rate(0, 0);
  if (msg.contains("throttle_rate") && msg["throttle_rate"].is_number()) {
    size_t throttle_rate_ms = msg["throttle_rate"];
    throttle_rate = rclcpp::Duration(0, throttle_rate_ms * 1000000);
  }
  std::string compression =
    (!msg.contains("compression") || !msg["compression"].is_string()) ? "none" : msg["compression"];

  auto sub_type = topics[topic][0];
  if (subscriptions_.count(topic) == 0) {
    
    topic_params params(topic, sub_type, history_depth, compression, throttle_rate);
    subscriptions_[topic] = connector_->subscribe_to_topic(
      client_id_, params, std::bind(&ClientHandler::subscription_callback, this, std::placeholders::_1, std::placeholders::_2));

    response["type"] = sub_type;
    response["result"] = true;
  }

  return true;
}

bool ClientHandler::unsubscribe_from_topic(const json & msg, json & response)
{
  response["op"] = "unsubscribe_response";

  std::string topic = msg["topic"];
  if (subscriptions_.count(topic) > 0) {
    subscriptions_[topic]();
    subscriptions_.erase(topic);
    response["result"] = true;
  }

  return true;
}

bool ClientHandler::advertise_topic(const json & msg, json & response)
{
  response["op"] = "advertise_response";
  if (!msg.contains("type") || !msg["type"].is_string()) {
    response["result"] = false;
    response["error"] = "No type specified";
    RCLCPP_ERROR(get_logger(), response["error"].dump().c_str());
    return true;
  }

  if (!msg.contains("topic") || !msg["topic"].is_string()) {
    response["result"] = false;
    response["error"] = "No topic specified";
    RCLCPP_ERROR(get_logger(), response["error"].dump().c_str());
    return true;
  }

  std::string topic = msg["topic"];
  std::string type = rws::message_type_to_ros2_style(msg["type"]);
  size_t history_depth = 10;
  if (msg.contains("history_depth") && msg["history_depth"].is_number()) {
    history_depth = msg["history_depth"];
  } else if (msg.contains("queue_size") && msg["queue_size"].is_number()) {
    history_depth = msg["queue_size"];
  }
  bool latch =
    (msg.contains("latch") && msg["latch"].is_boolean()) ? msg["latch"].get<bool>() : false;
  topic_params params(topic, type, history_depth, latch);

  if (publishers_.count(topic) == 0) {
    publishers_[topic] = connector_->advertise_topic(client_id_, params, publisher_cb_[topic]);
    publisher_type_[topic] = type;
    response["result"] = true;
  }

  return true;
}

bool ClientHandler::unadvertise_topic(const json & msg, json & response)
{
  response["op"] = "unadvertise_response";

  std::string topic = msg["topic"];
  if (publishers_.count(topic) > 0) {
    publishers_[topic]();
    publishers_.erase(topic);
    publisher_cb_.erase(topic);
    publisher_type_.erase(topic);
    response["result"] = true;
  }

  return true;
}

bool ClientHandler::publish_to_topic(const json & msg, json & response)
{
  response["op"] = "publish_response";

  if (!msg.contains("topic")) {
    response["false"] = true;
    response["error"] = "No topic specified";
    RCLCPP_ERROR(get_logger(), response["error"].dump().c_str());
    return true;
  }

  std::string topic = msg["topic"];

  if (publishers_.count(topic) > 0) {
    json msg_json = msg["msg"];
    std::string type = publisher_type_[topic];
    auto serialized_msg = rws::json_to_serialized_message(type, msg_json);
    publisher_cb_[topic](serialized_msg);
    response["result"] = true;
  } else {
    response["result"] = false;
    response["error"] = "Topic was not advertised";
    RCLCPP_ERROR(get_logger(), response["error"].dump().c_str());
  }

  return true;
}

bool ClientHandler::call_service(const json & msg, json & response)
{
  if (!msg.contains("service")) {
    RCLCPP_ERROR(get_logger(), "No service specified");
    return true;
  }
  std::string service = msg["service"];

  response["op"] = "service_response";
  response["service"] = service;
  response["result"] = false;

  if (service == "/rosapi/topics_and_raw_types" || service == "/rosapi/topics") {
    response["values"]["topics"] = json::array();
    response["values"]["types"] = json::array();

    std::map<std::string, std::vector<std::string>> topics = node_->get_topic_names_and_types();
    for (auto it = topics.begin(); it != topics.end(); ++it) {
      response["values"]["topics"].push_back(it->first);
      response["values"]["types"].push_back(it->second[0]);

      if (msg["service"] == "/rosapi/topics_and_raw_types") {
        response["values"]["typedefs_full_text"].push_back(
          rws::generate_message_meta(it->second[0], rosbridge_compatible_));
      }
    }
    response["result"] = true;
    return true;
  }

  if (service == "/rosapi/service_type") {
    std::string service_name = msg["args"]["service"];
    std::map<std::string, std::vector<std::string>> services = node_->get_service_names_and_types();
    if (services.find(service_name) == services.end()) {
      RCLCPP_ERROR(get_logger(), "Service not found: %s", service_name.c_str());
      return true;
    }

    std::string service_type = services[service_name][0];
    response["values"]["type"] = service_type;
    response["result"] = true;
    return true;
  }

  if (service == "/rosapi/nodes") {
    response["values"]["nodes"] = json::array();

    std::vector<std::string> nodes = node_->get_node_names();
    for (auto it = nodes.begin(); it != nodes.end(); ++it) {
      response["values"]["nodes"].push_back(*it);
    }

    response["result"] = true;
    return true;
  }

  if (service == "/rosapi/publishers") {
    response["values"]["publishers"] = json::array();

    std::vector<rclcpp::TopicEndpointInfo> publishers = node_->get_publishers_info_by_topic(msg["args"]["topic"]);
    for (const auto & pub_info : publishers) {
      response["values"]["publishers"].push_back(("/" + pub_info.node_name()).c_str());
    }

    response["result"] = true;
    return true;
  }

  if (service == "/rosapi/subscribers") {
    response["values"]["subscribers"] = json::array();

    std::vector<rclcpp::TopicEndpointInfo> subscribers = node_->get_subscriptions_info_by_topic(msg["args"]["topic"]);
    for (const auto & sub_info : subscribers) {
      response["values"]["subscribers"].push_back(("/" + sub_info.node_name()).c_str());
    }

    response["result"] = true;
    return true;
  }

  if (service == "/rosapi/node_details") {
    response["values"]["subscribing"] = json::array();
    response["values"]["publishing"] = json::array();
    response["values"]["services"] = json::array();

    auto [ns, node_name] = split_ns_node_name(msg["args"]["node"]);

    std::map<std::string, std::vector<std::string>> topics = node_->get_topic_names_and_types();
    for (auto it = topics.begin(); it != topics.end(); ++it) {
      auto subscribers = node_->get_subscriptions_info_by_topic(it->first);
      for (auto sub_it = subscribers.begin(); sub_it != subscribers.end(); ++sub_it) {
        std::string sub_node = sub_it->node_name();
        std::string sub_ns = sub_it->node_namespace() == "/" ? "" : sub_it->node_namespace();
        if (sub_ns + sub_node == ns + node_name) {
          response["values"]["subscribing"].push_back(it->first);
        }
      }

      auto publishers = node_->get_publishers_info_by_topic(it->first);
      for (auto pub_it = publishers.begin(); pub_it != publishers.end(); ++pub_it) {
        std::string pub_node = pub_it->node_name();
        std::string pub_ns = pub_it->node_namespace() == "/" ? "" : pub_it->node_namespace();
        if (pub_ns + pub_node == ns + node_name) {
          response["values"]["publishing"].push_back(it->first);
        }
      }
    }

    try {
      auto services = node_->get_service_names_and_types_by_node(node_name, ns);
      for (auto it = services.begin(); it != services.end(); ++it) {
        response["values"]["services"].push_back(it->first);
      }
    } catch (const std::exception & e) {
      RCLCPP_ERROR(
        get_logger(), "Exception while fetching services for node(%s), ns=%s, name=%s: %s",
        msg["args"]["node"].get<std::string>().c_str(), ns.c_str(), node_name.c_str(), e.what());
    }

    response["result"] = true;
    return true;
  }

  if (service == "/rosapi/topic_type") {
    std::string topic_name = msg["args"]["topic"].get<std::string>();
    std::map<std::string, std::vector<std::string>> topics = node_->get_topic_names_and_types(); 
    if (topics.find(topic_name) == topics.end()) {
      RCLCPP_ERROR(get_logger(), "Topic not found: %s", topic_name.c_str());
      return true;
    }

    response["values"]["type"] = topics[topic_name][0];
    response["result"] = true;
    return true;
  }

  if (service == "/rosapi/services_for_type") {
    std::string service_type = msg["args"]["type"].get<std::string>();
    auto service_name_and_types = node_->get_service_names_and_types();

    std::map<std::string, std::vector<std::string>> filtered_service_name_and_types;
    for (const auto &pair : service_name_and_types) {
      for (const auto &type : pair.second) {
        if (type == service_type) {
          filtered_service_name_and_types.insert(pair);
          break;
        }
      }
    }

    response["values"]["services"] = json::array();
    for (auto it = filtered_service_name_and_types.begin(); it != filtered_service_name_and_types.end(); ++it) {
      response["values"]["services"].push_back(it->first);
    }

    response["result"] = true;
    return true;
  }

  return call_external_service(msg, response);
}

bool ClientHandler::call_external_service(const json & msg, json & response)
{
  auto call_start = std::chrono::high_resolution_clock::now();
  std::string service_name = msg["service"];
  std::string service_type = msg["type"];
  std::string request_id = msg.contains("id") ? msg["id"].dump() : "no_id";

  // Extract trace information for diagnostics
  std::string trace_id = "no_trace";
  if (msg.contains("_trace") && msg["_trace"].contains("traceId")) {
    trace_id = msg["_trace"]["traceId"];
  }

  if (enable_timing_logs_) {
    RCLCPP_INFO(get_logger(), "[BRIDGE] [TRACE-%s] EXTERNAL_SERVICE_CALL: %s to %s",
                trace_id.c_str(), request_id.c_str(), service_name.c_str());
  }

  // Fast check: is service available in cache?
  auto cache_check_start = std::chrono::high_resolution_clock::now();
  if (!is_service_available(service_name)) {
    auto cache_check_time = std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::high_resolution_clock::now() - cache_check_start).count();

    if (enable_timing_logs_) {
      RCLCPP_WARN(get_logger(), "[BRIDGE] [TRACE-%s] SERVICE_NOT_FOUND: %s (%s) [cache check: %ldμs]",
                  trace_id.c_str(), request_id.c_str(), service_name.c_str(), cache_check_time);
    } else {
      RCLCPP_ERROR(get_logger(), "Service not found: %s", service_name.c_str());
    }
    response["op"] = "service_response";
    response["service"] = service_name;
    response["result"] = false;
    response["error"] = "Service not found: " + service_name;
    return true; // Request was handled, just failed
  }
  auto cache_check_time = std::chrono::duration_cast<std::chrono::microseconds>(
    std::chrono::high_resolution_clock::now() - cache_check_start).count();

  // Create or reuse client
  auto client_setup_start = std::chrono::high_resolution_clock::now();
  std::shared_ptr<rws::GenericClient> client;
  {
    std::lock_guard<std::mutex> lock(clients_mutex_);
    if (clients_.count(service_name) == 0) {
      clients_[service_name] = node_->create_generic_client(
        service_name, service_type, rmw_qos_profile_services_default, nullptr);
      if (enable_timing_logs_) {
        RCLCPP_INFO(get_logger(), "[BRIDGE] [TRACE-%s] CLIENT_CREATED: %s for %s",
                    trace_id.c_str(), request_id.c_str(), service_name.c_str());
      }
    }
    client = clients_[service_name];
  }
  auto client_setup_time = std::chrono::duration_cast<std::chrono::microseconds>(
    std::chrono::high_resolution_clock::now() - client_setup_start).count();

  // Quick availability check (non-blocking)
  auto availability_start = std::chrono::high_resolution_clock::now();
  if (!client->service_is_ready()) {
    auto availability_time = std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::high_resolution_clock::now() - availability_start).count();

    if (enable_timing_logs_) {
      RCLCPP_WARN(get_logger(), "[BRIDGE] [TRACE-%s] SERVICE_NOT_READY: %s (%s) [check: %ldμs]",
                  trace_id.c_str(), request_id.c_str(), service_name.c_str(), availability_time);
    } else {
      RCLCPP_WARN(get_logger(), "Service not ready: %s", service_name.c_str());
    }
    response["op"] = "service_response";
    response["service"] = service_name;
    response["result"] = false;
    response["error"] = "Service not ready: " + service_name;
    return true; // Request was handled, just failed
  }
  auto availability_time = std::chrono::duration_cast<std::chrono::microseconds>(
    std::chrono::high_resolution_clock::now() - availability_start).count();

  // Serialize request
  auto serialize_start = std::chrono::high_resolution_clock::now();
  auto serialized_req = json_to_serialized_service_request(service_type, msg["args"]);
  auto serialize_time = std::chrono::duration_cast<std::chrono::microseconds>(
    std::chrono::high_resolution_clock::now() - serialize_start).count();

  // Send async request - this returns immediately
  using ServiceResponseFuture = rws::GenericClient::SharedFuture;
  auto response_received_callback = [this, id = msg["id"], service_name, service_type,
                                     call_start, request_id, trace_id,
                                     enable_logs = enable_timing_logs_](ServiceResponseFuture future) {
    auto deserialize_start = std::chrono::high_resolution_clock::now();
    json response_json = serialized_service_response_to_json(service_type, future.get());
    auto deserialize_time = std::chrono::duration_cast<std::chrono::microseconds>(
      std::chrono::high_resolution_clock::now() - deserialize_start).count();

    json m = {
      {"id", id},
      {"op", "service_response"},
      {"service", service_name},
      {"values", response_json},
      {"result", true},
    };

    std::string json_str = m.dump();
    auto total_time = std::chrono::duration_cast<std::chrono::milliseconds>(
      std::chrono::high_resolution_clock::now() - call_start).count();

    if (enable_logs) {
      RCLCPP_INFO(this->get_logger(), "[BRIDGE] [TRACE-%s] SERVICE_RESPONSE: %s completed in %ldms [deserialize: %ldμs]",
                  trace_id.c_str(), request_id.c_str(), total_time, deserialize_time);

      // Parseable timing for end-to-end service call
      std::stringstream timing_msg;
      timing_msg << "RWS_TIMING|trace_id=" << trace_id
                 << "|service=" << service_name
                 << "|deserialize_us=" << deserialize_time
                 << "|total_e2e_ms=" << total_time;
      this->log_timing(timing_msg.str());
    }

    this->send_message(json_str);
  };

  auto send_start = std::chrono::high_resolution_clock::now();
  client->async_send_request(serialized_req, response_received_callback);
  auto send_time = std::chrono::duration_cast<std::chrono::microseconds>(
    std::chrono::high_resolution_clock::now() - send_start).count();

  auto total_sync_time = std::chrono::duration_cast<std::chrono::microseconds>(
    std::chrono::high_resolution_clock::now() - call_start).count();

  if (enable_timing_logs_) {
    // Structured log format for parsing: RWS_TIMING|phase|value_us
    RCLCPP_INFO(get_logger(), "[BRIDGE] [TRACE-%s] SERVICE_DISPATCHED: %s [total: %ldμs = cache: %ldμs + client: %ldμs + ready: %ldμs + serialize: %ldμs + send: %ldμs]",
                trace_id.c_str(), request_id.c_str(), total_sync_time,
                cache_check_time, client_setup_time, availability_time, serialize_time, send_time);

    // Parseable timing breakdown
    std::stringstream timing_msg;
    timing_msg << "RWS_TIMING|trace_id=" << trace_id
               << "|service=" << service_name
               << "|cache_check_us=" << cache_check_time
               << "|client_setup_us=" << client_setup_time
               << "|ready_check_us=" << availability_time
               << "|serialize_us=" << serialize_time
               << "|send_us=" << send_time
               << "|total_dispatch_us=" << total_sync_time;
    log_timing(timing_msg.str());
  }

  response["op"] = "call_service";
  response["result"] = true;
  return true;
}

void ClientHandler::update_service_cache()
{
  std::lock_guard<std::mutex> lock(service_cache_mutex_);
  auto now = std::chrono::steady_clock::now();
  auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(now - service_cache_time_).count();

  // Update if cache is empty (first call) or stale
  if (service_cache_.empty() || elapsed > SERVICE_CACHE_MS) {
    service_cache_ = node_->get_service_names_and_types();
    service_cache_time_ = now;

    if (enable_timing_logs_) {
      RCLCPP_DEBUG(get_logger(), "[BRIDGE] Service cache updated (%zu services)", service_cache_.size());
    }
  }
}

bool ClientHandler::is_service_available(const std::string& service_name)
{
  update_service_cache();

  std::lock_guard<std::mutex> lock(service_cache_mutex_);
  return service_cache_.find(service_name) != service_cache_.end();
}

void ClientHandler::log_timing(const std::string& message)
{
  if (!enable_timing_logs_) {
    return;
  }

  // Always log to ROS logger
  RCLCPP_INFO(get_logger(), "%s", message.c_str());

  // Optionally log to file if configured
  if (!timing_log_file_.empty()) {
    std::lock_guard<std::mutex> lock(timing_log_mutex_);
    std::ofstream logfile(timing_log_file_, std::ios::app);
    if (logfile.is_open()) {
      // Add timestamp
      auto now = std::chrono::system_clock::now();
      auto time_t_now = std::chrono::system_clock::to_time_t(now);
      auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        now.time_since_epoch()) % 1000;

      char timestamp[64];
      std::strftime(timestamp, sizeof(timestamp), "%Y-%m-%d %H:%M:%S", std::localtime(&time_t_now));

      logfile << timestamp << "." << std::setfill('0') << std::setw(3) << ms.count()
              << " " << message << std::endl;
    }
  }
}

}  // namespace rws