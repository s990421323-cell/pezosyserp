import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

const apiBaseUrl = String.fromEnvironment(
  'API_BASE_URL',
  defaultValue: 'http://localhost:8000',
);

void main() {
  runApp(const PezosysErpApp());
}

class PezosysErpApp extends StatelessWidget {
  const PezosysErpApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Pezosys ERP',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF1769E0),
          surface: const Color(0xFFF5F8FC),
        ),
        useMaterial3: true,
      ),
      home: const DashboardPage(),
    );
  }
}

class DashboardPage extends StatefulWidget {
  const DashboardPage({super.key});

  @override
  State<DashboardPage> createState() => _DashboardPageState();
}

class _DashboardPageState extends State<DashboardPage> {
  String _apiStatus = 'Checking connection…';
  bool _isConnected = false;

  @override
  void initState() {
    super.initState();
    _checkApi();
  }

  Future<void> _checkApi() async {
    try {
      final response = await http
          .get(Uri.parse('$apiBaseUrl/health'))
          .timeout(const Duration(seconds: 5));
      final body = jsonDecode(response.body);
      if (!mounted) return;
      setState(() {
        _isConnected = response.statusCode == 200 &&
            body is Map<String, dynamic> &&
            body['status'] == 'ok';
        _apiStatus = _isConnected ? 'API connected' : 'API returned an error';
      });
    } on http.ClientException catch (error) {
      if (!mounted) return;
      setState(() {
        _isConnected = false;
        _apiStatus = 'Could not reach API: ${error.message}';
      });
    } on FormatException catch (error) {
      if (!mounted) return;
      setState(() {
        _isConnected = false;
        _apiStatus = 'Invalid API response: ${error.message}';
      });
    } on TimeoutException {
      if (!mounted) return;
      setState(() {
        _isConnected = false;
        _apiStatus = 'API request timed out';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text(
          'Pezosys ERP',
          style: TextStyle(fontWeight: FontWeight.w700),
        ),
        backgroundColor: Theme.of(context).colorScheme.surface,
      ),
      body: SafeArea(
        child: RefreshIndicator(
          onRefresh: _checkApi,
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              Text(
                'Welcome to your workspace',
                style: Theme.of(context).textTheme.headlineMedium?.copyWith(
                      fontWeight: FontWeight.w700,
                    ),
              ),
              const SizedBox(height: 8),
              Text(
                'Your business overview will live here.',
                style: Theme.of(context).textTheme.bodyLarge?.copyWith(
                      color: Theme.of(context).colorScheme.onSurfaceVariant,
                    ),
              ),
              const SizedBox(height: 24),
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(20),
                  child: Row(
                    children: [
                      Icon(
                        _isConnected
                            ? Icons.cloud_done_outlined
                            : Icons.cloud_off_outlined,
                        color: _isConnected
                            ? Colors.green.shade700
                            : Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                      const SizedBox(width: 12),
                      Expanded(child: Text(_apiStatus)),
                      IconButton(
                        onPressed: _checkApi,
                        tooltip: 'Check connection',
                        icon: const Icon(Icons.refresh),
                      ),
                    ],
                  ),
                ),
              ),
              const SizedBox(height: 16),
              const _StarterCard(
                icon: Icons.inventory_2_outlined,
                title: 'Inventory',
                description: 'Product and stock management will be added here.',
              ),
              const SizedBox(height: 12),
              const _StarterCard(
                icon: Icons.receipt_long_outlined,
                title: 'Sales',
                description: 'Sales and invoicing will be added here.',
              ),
              const SizedBox(height: 12),
              const _StarterCard(
                icon: Icons.people_outline,
                title: 'Customers',
                description: 'Customer management will be added here.',
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _StarterCard extends StatelessWidget {
  const _StarterCard({
    required this.icon,
    required this.title,
    required this.description,
  });

  final IconData icon;
  final String title;
  final String description;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: ListTile(
        contentPadding: const EdgeInsets.symmetric(horizontal: 20, vertical: 8),
        leading: Icon(icon, color: Theme.of(context).colorScheme.primary),
        title: Text(title, style: const TextStyle(fontWeight: FontWeight.w600)),
        subtitle: Text(description),
      ),
    );
  }
}
