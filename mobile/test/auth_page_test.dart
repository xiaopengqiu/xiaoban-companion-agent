import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:companion_mobile/auth_page.dart';

void main() {
  testWidgets(
    'register requires explicit data consent before sending credentials',
    (tester) async {
      var requests = 0;
      await tester.pumpWidget(
        MaterialApp(
          home: AuthPage(
            initialServer: 'http://127.0.0.1:8765',
            onSubmit: (_, __, ___) async {
              requests++;
            },
          ),
        ),
      );
      await tester.tap(find.text('注册'));
      await tester.pumpAndSettle();
      await tester.enterText(find.byKey(const Key('name')), '小林');
      await tester.enterText(find.byKey(const Key('email')), 'lin@example.com');
      await tester.enterText(
        find.byKey(const Key('password')),
        'long-enough-password',
      );
      await tester.ensureVisible(find.byKey(const Key('submit-auth')));
      await tester.tap(find.byKey(const Key('submit-auth')));
      await tester.pumpAndSettle();
      expect(requests, 0);
      expect(find.textContaining('请先同意'), findsOneWidget);
    },
  );

  testWidgets('isolated demo needs no real email or password', (tester) async {
    String? mode;
    await tester.pumpWidget(
      MaterialApp(
        home: AuthPage(
          initialServer: 'http://127.0.0.1:8765',
          onSubmit: (_, method, __) async {
            mode = method;
          },
        ),
      ),
    );
    await tester.ensureVisible(find.text('体验独立演示'));
    await tester.tap(find.text('体验独立演示'));
    await tester.pumpAndSettle();
    expect(mode, 'demo');
  });
}
