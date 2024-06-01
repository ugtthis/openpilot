#pragma once

#include <QWidget>
#include <QPainter>
#include <QTimer>
#include "cereal/gen/cpp/log.capnp.h"

struct AlertProperties {
  QColor outerColor;
  QColor middleColor;
  QColor innerColor;

  QColor outerBorderColor;
  QColor middleBorderColor;
  QColor innerBorderColor;

  QColor outerShadowColor;
  QColor middleShadowColor;
  QColor innerShadowColor;

  int outerShadowOpacity;
  int middleShadowOpacity;
  int innerShadowOpacity;

  int outerShadowBlurRadius;
  int middleShadowBlurRadius;
  int innerShadowBlurRadius;

  int outerBorderThickness;
  int middleBorderThickness;
  int innerBorderThickness;

  int shadowX;
  int shadowY;

  // Properties for alert ball
  QColor alertBallOuterColor;
  QColor alertBallInnerColor;

  QColor alertBallOuterBorderColor;
  QColor alertBallInnerBorderColor;

  int alertBallOuterBorderThickness;
  int alertBallInnerBorderThickness;
};

class DriverAlertDial : public QWidget {
  Q_OBJECT

public:
  explicit DriverAlertDial(QWidget *parent = nullptr);
  void updateState(cereal::ModelDataV2::ConfidenceClass confidence, float steering_torque,
                                                                    float brake_preassure,
                                                                    float acceleration,
                                                                    bool engaged);
  void setEngagedStatus(bool engaged);

protected:
  void paintEvent(QPaintEvent *event) override;

private:
  cereal::ModelDataV2::ConfidenceClass confidence;
  bool is_engaged;
  AlertProperties getAlertProperties(cereal::ModelDataV2::ConfidenceClass confidence) const;
  AlertProperties getAlertPropertiesForDisengaged() const;
  QColor getAlertColor(cereal::ModelDataV2::ConfidenceClass confidence);
  QPointF calculateAlertBallPosition() const;

  float steering_torque;
  float brake_pressure;
  float acceleration;

  void drawCircle(QPainter &painter,
                  const QPointF &center,
                  const QColor &fill_color,
                  const QColor &border_color,
                  const QColor &shadow_color,
                  int border_thickness,
                  int radius,
                  int shadow_blur_radius,
                  int shadow_opacity,
                  int shadow_x,
                  int shadow_y);

  QRadialGradient createRadialGradient(const QPointF &center,
                                       const QColor &color,
                                       int radius,
                                       int blur_radius,
                                       int opacity,
                                       int x_offset,
                                       int y_offset);
};