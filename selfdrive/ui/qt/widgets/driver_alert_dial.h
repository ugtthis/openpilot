#pragma once

#include <QWidget>
#include <QPainter>
#include <QTimer>
#include "cereal/gen/cpp/log.capnp.h"

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
  void drawCircle(QPainter &painter, const QPointF &center, int radius, const QColor &fill_color, const QColor &border_color, int border_thickness);
};