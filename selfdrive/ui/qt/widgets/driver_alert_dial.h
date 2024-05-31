#pragma once

#include <QWidget>
#include <QPainter>
#include <QTimer>
#include "cereal/gen/cpp/log.capnp.h"

struct AlertProperties {
  QColor outerColor;
  QColor middleColor;
  QColor innerColor;
  QColor borderColor;
  QColor shadowColor;

  int shadowBlurRadius;
  int shadowX;
  int shadowY;
  int outerBorderThickness;
  int middleBorderThickness;
  int innerBorderThickness;
};

class DriverAlertDial : public QWidget {
  Q_OBJECT

public:
  explicit DriverAlertDial(QWidget *parent = nullptr);
  void updateState(cereal::ModelDataV2::ConfidenceClass confidence, float steering_torque, float brake_preassure, float acceleration);

protected:
  void paintEvent(QPaintEvent *event) override;

private:
  cereal::ModelDataV2::ConfidenceClass confidence;
  float steering_torque;
  float brake_pressure;
  float acceleration;
  QColor getAlertColor(cereal::ModelDataV2::ConfidenceClass confidence);
  QPointF calculateAlertBallPosition() const;
  AlertProperties getAlertProperties(cereal::ModelDataV2::ConfidenceClass confidence) const;
  void drawCircle(QPainter &painter,
                  const QPointF &center,
                  int radius,
                  const QColor &fill_color,
                  const QColor &border_color,
                  int border_thickness,
                  const QColor &shadow_color,
                  int shadow_blur_radius,
                  int shadow_x,
                  int shadow_y);
};