#pragma once

#include <QWidget>
#include <QVBoxLayout>
#include <QTimer>
#include <QPainter>
#include <QRadialGradient>

#include "cereal/messaging/messaging.h"
#include "selfdrive/ui/ui.h"

struct AlertProperties {
  QColor borderColor;
  QColor fillColor;
  QColor circleColors[7];
  QColor iconColor;
  QColor textColor;
  int borderWidth;
  QColor shadowColor;
  float shadowOpacity;
  int blurRadius;
};

class DriverAlertCluster : public QWidget {
  Q_OBJECT

public:
  explicit DriverAlertCluster(UIState* ui_State, QWidget* parent = nullptr);
  void updateAlertLevel(const UIState &s);

  cereal::ModelDataV2::DisengagePredictions::Reader getDisengagePredictions(const SubMaster &sm) const;

protected:
  void paintEvent(QPaintEvent* event) override;
  int calculateAlertLevel(const capnp::List<float>::Reader &probs);
  AlertProperties getAlertProperties(int alertLevel);

  void drawAlertBar(QPainter &painter, const QString &labelText,
                                       const capnp::List<float>::Reader &probs,
                                       int yOffset);

private:
  QTimer *updateTimer;

  int steeringAlertLevel;
  int brakeAlertLevel;
  int gasAlertLevel;

  UIState* ui_State;

  QRadialGradient createRadialGradient(const QPointF &center,
                                       int radius,
                                       int blur_radius,
                                       const QColor &color,
                                       int opacity,
                                       int x_offset,
                                       int y_offset);
};