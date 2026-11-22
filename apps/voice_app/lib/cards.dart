import 'dart:math' as math;

import 'package:flutter/material.dart';

/// A card the secretary shows instead of reading it out (apps/secretary/secretary/screen.py):
/// list, table, bar, line, stats or text. Drawn with plain Flutter, no chart package.
class SecretaryCard extends StatelessWidget {
  const SecretaryCard({super.key, required this.card});

  final Map<String, dynamic> card;

  List<Map<String, dynamic>> get items => [
    for (final item in (card['items'] as List? ?? const []))
      if (item is Map) Map<String, dynamic>.from(item),
  ];

  String get unit => card['unit'] as String? ?? '';

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final kind = card['kind'] as String? ?? 'text';
    final title = card['title'] as String? ?? '';
    final note = card['note'] as String?;
    return Container(
      key: Key('card-$kind'),
      width: double.infinity,
      margin: const EdgeInsets.only(top: 6),
      padding: const EdgeInsets.fromLTRB(14, 12, 14, 12),
      decoration: BoxDecoration(
        color: scheme.surface,
        border: Border.all(color: scheme.outlineVariant),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (title.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                children: [
                  Icon(_icon(kind), size: 18, color: scheme.primary),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      title,
                      style: text.titleSmall?.copyWith(
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                  ),
                ],
              ),
            ),
          switch (kind) {
            'list' => _list(context),
            'table' => _table(context),
            'bar' => _bars(context),
            'line' => _line(context),
            'stats' => _stats(context),
            _ => _body(context),
          },
          if (note != null && note.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                note,
                style: text.bodySmall?.copyWith(color: scheme.outline),
              ),
            ),
        ],
      ),
    );
  }

  static IconData _icon(String kind) => switch (kind) {
    'list' => Icons.checklist,
    'table' => Icons.table_chart_outlined,
    'bar' => Icons.bar_chart,
    'line' => Icons.show_chart,
    'stats' => Icons.insights,
    _ => Icons.notes,
  };

  /// 1234567 -> "1,234,567"; 12.5 -> "12.5"
  static String number(num value) {
    final whole = value == value.roundToDouble();
    final text = whole
        ? value.round().abs().toString()
        : value.abs().toStringAsFixed(1);
    final parts = text.split('.');
    final digits = parts[0].replaceAllMapped(
      RegExp(r'\B(?=(\d{3})+(?!\d))'),
      (_) => ',',
    );
    return '${value < 0 ? '-' : ''}$digits${parts.length > 1 ? '.${parts[1]}' : ''}';
  }

  String _value(Map<String, dynamic> item) {
    final value = item['value'];
    if (value is num) return '${number(value)}$unit';
    return item['detail'] as String? ?? '';
  }

  Widget _list(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final rows = items;
    return Column(
      children: [
        for (var i = 0; i < rows.length; i++)
          Container(
            padding: const EdgeInsets.symmetric(vertical: 8),
            decoration: i == 0
                ? null
                : BoxDecoration(
                    border: Border(
                      top: BorderSide(color: scheme.outlineVariant, width: 0.5),
                    ),
                  ),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                SizedBox(
                  width: 24,
                  child: Text(
                    '${i + 1}',
                    style: TextStyle(
                      color: scheme.primary,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
                ),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        rows[i]['label'] as String? ?? '',
                        style: const TextStyle(fontSize: 15, height: 1.3),
                      ),
                      if ((rows[i]['detail'] as String? ?? '').isNotEmpty)
                        Text(
                          rows[i]['detail'] as String,
                          style: TextStyle(
                            fontSize: 13,
                            color: scheme.onSurfaceVariant,
                          ),
                        ),
                    ],
                  ),
                ),
                if (rows[i]['value'] is num)
                  Text(
                    _value(rows[i]),
                    style: const TextStyle(fontWeight: FontWeight.w600),
                  ),
                if ((rows[i]['badge'] as String? ?? '').isNotEmpty)
                  Container(
                    margin: const EdgeInsets.only(left: 6),
                    padding: const EdgeInsets.symmetric(
                      horizontal: 8,
                      vertical: 2,
                    ),
                    decoration: BoxDecoration(
                      color: scheme.secondaryContainer,
                      borderRadius: BorderRadius.circular(10),
                    ),
                    child: Text(
                      rows[i]['badge'] as String,
                      style: TextStyle(
                        fontSize: 12,
                        color: scheme.onSecondaryContainer,
                      ),
                    ),
                  ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _table(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final columns = [
      for (final c in (card['columns'] as List? ?? const [])) '$c',
    ];
    final rows = [
      for (final row in (card['rows'] as List? ?? const []))
        if (row is List) [for (final cell in row) '$cell'],
    ];
    final width = math.max(
      columns.length,
      rows.fold<int>(0, (most, row) => math.max(most, row.length)),
    );
    String at(List<String> row, int i) => i < row.length ? row[i] : '';
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: DataTable(
        headingRowHeight: columns.isEmpty ? 0 : 36,
        dataRowMinHeight: 34,
        dataRowMaxHeight: 56,
        horizontalMargin: 4,
        columnSpacing: 18,
        headingTextStyle: TextStyle(
          fontWeight: FontWeight.w700,
          color: scheme.onSurfaceVariant,
        ),
        columns: [
          for (var i = 0; i < width; i++)
            DataColumn(label: Text(i < columns.length ? columns[i] : '')),
        ],
        rows: [
          for (final row in rows)
            DataRow(
              cells: [
                for (var i = 0; i < width; i++) DataCell(Text(at(row, i))),
              ],
            ),
        ],
      ),
    );
  }

  /// Horizontal bars: labels stay readable on a narrow phone, values at the end of each bar.
  Widget _bars(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final rows = items;
    final most = rows.fold<double>(
      0,
      (most, item) =>
          math.max(most, ((item['value'] as num?) ?? 0).abs().toDouble()),
    );
    return Column(
      children: [
        for (final item in rows)
          Padding(
            padding: const EdgeInsets.symmetric(vertical: 4),
            child: Row(
              children: [
                SizedBox(
                  width: 84,
                  child: Text(
                    item['label'] as String? ?? '',
                    maxLines: 2,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(fontSize: 13),
                  ),
                ),
                Expanded(
                  child: LayoutBuilder(
                    builder: (context, box) {
                      final value = ((item['value'] as num?) ?? 0)
                          .abs()
                          .toDouble();
                      final share = most == 0 ? 0.0 : value / most;
                      return Row(
                        children: [
                          Container(
                            height: 18,
                            width: math.max(3, (box.maxWidth - 70) * share),
                            decoration: BoxDecoration(
                              color: scheme.primary,
                              borderRadius: BorderRadius.circular(4),
                            ),
                          ),
                          const SizedBox(width: 6),
                          Flexible(
                            child: Text(
                              _value(item),
                              overflow: TextOverflow.fade,
                              softWrap: false,
                              style: const TextStyle(
                                fontSize: 13,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                        ],
                      );
                    },
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _line(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final rows = items;
    final values = [
      for (final item in rows) ((item['value'] as num?) ?? 0).toDouble(),
    ];
    final last = values.isEmpty ? 0.0 : values.last;
    final first = values.isEmpty ? 0.0 : values.first;
    final change = last - first;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text.rich(
          TextSpan(
            children: [
              TextSpan(
                text: '${number(last)}$unit',
                style: const TextStyle(
                  fontSize: 22,
                  fontWeight: FontWeight.w700,
                ),
              ),
              TextSpan(
                text:
                    '  ${change >= 0 ? '▲' : '▼'} ${number(change.abs())}$unit',
                style: TextStyle(
                  fontSize: 13,
                  color: change >= 0 ? scheme.primary : scheme.error,
                ),
              ),
            ],
          ),
        ),
        const SizedBox(height: 8),
        SizedBox(
          height: 120,
          width: double.infinity,
          child: CustomPaint(
            painter: _LinePainter(
              values,
              scheme.primary,
              scheme.outlineVariant,
            ),
          ),
        ),
        const SizedBox(height: 4),
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            for (final i in {0, rows.length ~/ 2, rows.length - 1})
              if (i >= 0 && i < rows.length)
                Text(
                  rows[i]['label'] as String? ?? '',
                  style: TextStyle(fontSize: 11, color: scheme.outline),
                ),
          ],
        ),
      ],
    );
  }

  Widget _stats(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        for (final item in items)
          Container(
            constraints: const BoxConstraints(minWidth: 96),
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
            decoration: BoxDecoration(
              color: scheme.primaryContainer.withValues(alpha: 0.5),
              borderRadius: BorderRadius.circular(12),
            ),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  item['label'] as String? ?? '',
                  style: TextStyle(
                    fontSize: 12,
                    color: scheme.onSurfaceVariant,
                  ),
                ),
                Text(
                  _value(item),
                  style: const TextStyle(
                    fontSize: 22,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                if (item['value'] is num &&
                    (item['detail'] as String? ?? '').isNotEmpty)
                  Text(
                    item['detail'] as String,
                    style: TextStyle(fontSize: 11, color: scheme.outline),
                  ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _body(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    final lines = (card['body'] as String? ?? '').split('\n');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final line in lines)
          if (line.trim().isEmpty)
            const SizedBox(height: 6)
          else if (RegExp(r'^\s*[-•*]\s+').hasMatch(line))
            Padding(
              padding: const EdgeInsets.only(left: 4, bottom: 3),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('•  ', style: TextStyle(color: scheme.primary)),
                  Expanded(
                    child: SelectableText(
                      line.replaceFirst(RegExp(r'^\s*[-•*]\s+'), ''),
                      style: const TextStyle(fontSize: 15, height: 1.4),
                    ),
                  ),
                ],
              ),
            )
          else
            Padding(
              padding: const EdgeInsets.only(bottom: 3),
              child: SelectableText(
                line,
                style: const TextStyle(fontSize: 15, height: 1.45),
              ),
            ),
      ],
    );
  }
}

class _LinePainter extends CustomPainter {
  _LinePainter(this.values, this.color, this.grid);

  final List<double> values;
  final Color color;
  final Color grid;

  @override
  void paint(Canvas canvas, Size size) {
    if (values.length < 2) return;
    final low = values.reduce(math.min);
    final high = values.reduce(math.max);
    final span = high - low == 0 ? 1.0 : high - low;
    Offset at(int i) => Offset(
      size.width * i / (values.length - 1),
      size.height - 6 - (size.height - 12) * (values[i] - low) / span,
    );
    final gridPaint = Paint()
      ..color = grid
      ..strokeWidth = 0.5;
    for (final y in [6.0, size.height / 2, size.height - 6]) {
      canvas.drawLine(Offset(0, y), Offset(size.width, y), gridPaint);
    }
    final path = Path()..moveTo(at(0).dx, at(0).dy);
    for (var i = 1; i < values.length; i++) {
      path.lineTo(at(i).dx, at(i).dy);
    }
    final fill = Path.from(path)
      ..lineTo(size.width, size.height)
      ..lineTo(0, size.height)
      ..close();
    canvas.drawPath(fill, Paint()..color = color.withValues(alpha: 0.12));
    canvas.drawPath(
      path,
      Paint()
        ..color = color
        ..strokeWidth = 2.5
        ..style = PaintingStyle.stroke
        ..strokeJoin = StrokeJoin.round,
    );
    canvas.drawCircle(at(values.length - 1), 4, Paint()..color = color);
  }

  @override
  bool shouldRepaint(_LinePainter old) =>
      old.values != values || old.color != color;
}

/// What the owner attached, as it shows in their own bubble: the photo, or the recording.
class AttachmentView extends StatelessWidget {
  const AttachmentView({super.key, required this.shown, this.image});

  final Map<String, dynamic> shown;
  final Widget? image;

  static String duration(num seconds) {
    final total = seconds.round();
    return total >= 60 ? '${total ~/ 60}분 ${total % 60}초' : '$total초';
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    if (shown['kind'] == 'image' && image != null) {
      return ClipRRect(
        borderRadius: BorderRadius.circular(12),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxHeight: 240),
          child: image,
        ),
      );
    }
    final seconds = shown['seconds'];
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(
          shown['kind'] == 'image' ? Icons.image_outlined : Icons.graphic_eq,
          color: scheme.onPrimaryContainer,
        ),
        const SizedBox(width: 8),
        Flexible(
          child: Text(
            shown['kind'] == 'image'
                ? '사진'
                : '녹음 ${seconds is num ? duration(seconds) : ''}',
            style: TextStyle(
              fontWeight: FontWeight.w600,
              color: scheme.onPrimaryContainer,
            ),
          ),
        ),
      ],
    );
  }
}
