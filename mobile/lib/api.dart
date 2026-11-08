import 'dart:async';
import 'dart:convert';
import 'package:http/http.dart' as http;

class ApiException implements Exception {
  const ApiException(this.message, {this.statusCode});
  final String message;
  final int? statusCode;
  @override
  String toString() => message;
}

String normalizeServerUrl(String value, {bool requireHttps = false}) {
  var input = value.trim().replaceFirst(RegExp(r'/+$'), '');
  if (input.endsWith('/api')) input = input.substring(0, input.length - 4);
  final uri = Uri.tryParse(input);
  if (uri == null ||
      !['http', 'https'].contains(uri.scheme) ||
      uri.host.isEmpty ||
      uri.userInfo.isNotEmpty ||
      uri.hasQuery ||
      uri.hasFragment) {
    throw const ApiException('请输入有效的服务地址，例如 https://companion.example.com');
  }
  if (requireHttps && uri.scheme != 'https') {
    throw const ApiException('正式版本需要 HTTPS 服务地址。局域网 HTTP 仅用于调试构建。');
  }
  return input;
}

Map<String, dynamic> decodeObject(dynamic value) {
  if (value is! Map<String, dynamic>) {
    throw const ApiException('服务返回了无法识别的数据，请检查服务地址和版本。');
  }
  return value;
}

List<Map<String, dynamic>> decodeItems(Map<String, dynamic> response) {
  final items = response['items'];
  if (items is! List) throw const ApiException('服务返回的列表格式不正确。');
  return items.map(decodeObject).toList(growable: false);
}

String _errorDetail(dynamic detail) {
  if (detail is String) return detail;
  if (detail is List) {
    return detail
        .map((e) => e is Map ? (e['msg'] ?? '请求数据无效').toString() : e.toString())
        .join('；');
  }
  return '服务暂时无法完成请求，请稍后重试。';
}

class CompanionApi {
  CompanionApi(String baseUrl, {http.Client? client, bool requireHttps = false})
    : baseUrl = normalizeServerUrl(baseUrl, requireHttps: requireHttps),
      _client = client ?? http.Client();
  final String baseUrl;
  final http.Client _client;
  String? token;
  Future<Map<String, dynamic>> get(String path) => request('GET', path);
  Future<Map<String, dynamic>> post(String path, Map<String, dynamic> body) =>
      request('POST', path, body);
  Future<Map<String, dynamic>> patch(String path, Map<String, dynamic> body) =>
      request('PATCH', path, body);
  Future<Map<String, dynamic>> delete(
    String path, [
    Map<String, dynamic>? body,
  ]) => request('DELETE', path, body);

  Future<Map<String, dynamic>> request(
    String method,
    String path, [
    Map<String, dynamic>? body,
  ]) async {
    if (!path.startsWith('/') || path.contains('://')) {
      throw const ApiException('无效的接口路径。');
    }
    final request = http.Request(method, Uri.parse('$baseUrl/api$path'));
    request.headers['Accept'] = 'application/json';
    if (token != null) request.headers['Authorization'] = 'Bearer $token';
    if (body != null) {
      request.headers['Content-Type'] = 'application/json; charset=utf-8';
      request.body = jsonEncode(body);
    }
    try {
      final response = await http.Response.fromStream(
        await _client.send(request),
      ).timeout(const Duration(seconds: 35));
      dynamic decoded;
      try {
        decoded = response.bodyBytes.isEmpty
            ? <String, dynamic>{}
            : jsonDecode(utf8.decode(response.bodyBytes));
      } on FormatException {
        throw ApiException(
          '服务响应格式不正确（HTTP ${response.statusCode}）。请确认地址指向陪伴服务。',
          statusCode: response.statusCode,
        );
      }
      if (response.statusCode < 200 || response.statusCode >= 300) {
        throw ApiException(
          _errorDetail(decoded is Map ? decoded['detail'] : null),
          statusCode: response.statusCode,
        );
      }
      return decodeObject(decoded);
    } on TimeoutException {
      throw const ApiException('连接超时。请检查手机与服务的网络连接，然后重试。');
    } on http.ClientException {
      throw const ApiException('无法连接陪伴服务。请检查服务地址及网络。');
    }
  }

  void close() => _client.close();
}

class WearableConnection {
  const WearableConnection({
    required this.host,
    required this.userId,
    required this.accessToken,
    required this.refreshToken,
  });
  final String host;
  final String userId;
  final String accessToken;
  final String refreshToken;
  factory WearableConnection.fromJson(Map<String, dynamic> data) {
    if (data['configured'] != true) {
      throw ApiException(
        (data['detail'] ?? '服务端尚未配置 Open Wearables。').toString(),
      );
    }
    String field(String key) {
      final value = data[key];
      if (value is! String || value.trim().isEmpty) {
        throw const ApiException('服务未返回完整的健康同步凭证，未连接设备。');
      }
      return value;
    }

    return WearableConnection(
      host: normalizeServerUrl(field('host')),
      userId: field('wearable_user_id'),
      accessToken: field('access_token'),
      refreshToken: field('refresh_token'),
    );
  }
}
